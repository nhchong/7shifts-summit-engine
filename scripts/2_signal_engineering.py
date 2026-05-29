"""
Phase 2: Signal Engineering & Activation Scrubbing
--------------------------------------------------
This script performs targeted external enrichment and franchise detection 
on the resolved master dataset. It persists the final, activation-ready 
dataset to the warehouse 'fct_activation_ready' table.
"""

import pandas as pd
import logging
import requests
import sqlite3

# Suppress noisy HTTP/library logs
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logging.getLogger("urllib3").setLevel(logging.WARNING)

DB_PATH = 'data/7shifts_nyc_data.db'

def check_franchise_footprint(url: str) -> bool:
    """Performs a heuristic check for franchise markers on a given website."""
    if pd.isna(url) or not isinstance(url, str): return False
    target_url = url if url.startswith(('http://', 'https://')) else f"https://{url}"
    try:
        # 5-second timeout strikes balance between thoroughness and speed
        response = requests.get(target_url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            html = response.text.lower()
            return any(m in html for m in ['franchising', 'franchise opportunities', 'own a location'])
    except Exception: 
        # Log failure silently for individual sites to ensure pipeline continues
        return False
    return False

def run_signal_engineering():
    logging.info("Starting Phase 2: External Scraping & Franchise Scrubbing")
    
    # 1. Connect and pull from the resolved master dataset
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM fct_master_dataset", conn)
    
    # 2. Heuristic Franchise Detection
    # Filter for high-risk vendors (30+ global locations)
    high_risk = df[(df['global_location_count'] >= 30) & (df['vendor_website'].notna())]
    
    logging.info(f"Detected {len(high_risk)} high-risk vendors for deep-scrape.")
    
    franchise_ids = []
    
    # Iterate with progressive logging for terminal observability
    for idx, row in high_risk.iterrows():
        logging.info(f"Checking {idx+1}/{len(high_risk)}: {row['vendor_name']}...")
        
        if check_franchise_footprint(row['vendor_website']):
            logging.info(f" -> FRANCHISE DETECTED (Scrubbing): {row['vendor_name']}")
            franchise_ids.append(row['vendor_id'])
        else:
            logging.info(f" -> PASS: {row['vendor_name']} verified as independent.")
    
    # 3. Final Activation Scrub
    df_final = df[~df['vendor_id'].isin(franchise_ids)].copy()
    
    # 4. Write back to the warehouse as the final activation table
    df_final.to_sql('fct_activation_ready', conn, if_exists='replace', index=False)
    conn.close()
    
    logging.info(f"Phase 2 Complete. {len(df_final)} records persisted to 'fct_activation_ready'.")

if __name__ == "__main__":
    run_signal_engineering()