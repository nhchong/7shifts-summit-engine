"""
Phase 2: Signal Engineering & Activation Scrubbing
--------------------------------------------------
This script performs targeted external enrichment and franchise detection 
using a headless browser to render SPA frameworks. It augments the dataset
with franchise classifications and persists the entirety to 'fct_activation_ready'.
"""

import pandas as pd
import logging
import sqlite3
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_sync

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DB_PATH = 'data/7shifts_nyc_data.db'

# Strict regex patterns to avoid false positives on press articles
FRANCHISE_KEYWORDS = [
    r'\bfranchise opportunities\b',
    r'\bown a franchise\b',
    r'\bbecome a franchisee\b',
    r'\bfranchise information\b',
    r'\bown a location\b',
    r'\bfranchising with us\b'
]
FRANCHISE_PATTERN = re.compile('|'.join(FRANCHISE_KEYWORDS), re.IGNORECASE)

def run_signal_engineering():
    logging.info("Starting Phase 2: External Scraping & Franchise Scrubbing")
    
    # 1. Connect and pull from the resolved master dataset
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM fct_master_dataset", conn)
    
    # 2. Initialize Augmentation Columns (Fixing the Pandas dtype warning)
    df['is_franchise'] = pd.Series(dtype='object')
    df['is_franchise'] = False # Default to False, but column allows strings now
    df['franchise_evidence'] = None
    df['scrape_status'] = 'UNATTEMPTED'
    
    # 3. Identify High-Risk Vendors (7+ locations with valid websites)
    mask = (df['global_location_count'] >= 7) & (df['vendor_website'].notna()) & (df['vendor_website'] != '')
    high_risk_indices = df[mask].index
    
    logging.info(f"Detected {len(high_risk_indices)} high-risk vendors for deep-scrape.")
    
    # 4. Headless Browser Execution
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        for idx in high_risk_indices:
            vendor_name = df.at[idx, 'vendor_name']
            raw_url = df.at[idx, 'vendor_website']
            
            target_url = raw_url if raw_url.startswith(('http://', 'https://')) else f"https://{raw_url}"
            logging.info(f"Checking {vendor_name} ({target_url})...")
            
            try:
                # INJECT STEALTH HERE TO BYPASS WAF DROPS
                stealth_sync(page)
                
                # FIX: Changed to domcontentloaded to avoid infinite tracker loops
                response = page.goto(target_url, timeout=20000, wait_until='domcontentloaded')
                
                if response is None:
                    df.at[idx, 'scrape_status'] = 'FAILED_NO_RESPONSE'
                    df.at[idx, 'is_franchise'] = 'UNKNOWN'
                    logging.warning(f" -> UNKNOWN: {vendor_name} (No response from server)")
                    continue
                    
                status = response.status
                df.at[idx, 'scrape_status'] = str(status)
                
                if status >= 400:
                    df.at[idx, 'is_franchise'] = 'UNKNOWN'
                    logging.warning(f" -> UNKNOWN: {vendor_name} (HTTP {status})")
                    continue
                    
                # Extract the physically rendered innerText
                body_text = page.inner_text('body')
                
                # Execute strict heuristic check
                match = FRANCHISE_PATTERN.search(body_text)
                
                if match:
                    df.at[idx, 'is_franchise'] = True
                    df.at[idx, 'franchise_evidence'] = match.group(0)
                    logging.info(f" -> FRANCHISE DETECTED: {vendor_name} (Evidence: '{match.group(0)}')")
                else:
                    df.at[idx, 'is_franchise'] = False
                    logging.info(f" -> PASS: {vendor_name} verified as independent.")
                    
            except PlaywrightTimeoutError:
                df.at[idx, 'scrape_status'] = 'TIMEOUT'
                df.at[idx, 'is_franchise'] = 'UNKNOWN'
                logging.warning(f" -> UNKNOWN: {vendor_name} (Timeout - Site too slow or blocked bot)")
            except Exception as e:
                # Extract the exact Chromium network reason (e.g., net::ERR_CONNECTION_RESET)
                error_msg = str(e).split('\n')[0] 
                df.at[idx, 'scrape_status'] = f"ERROR: {error_msg}"
                df.at[idx, 'is_franchise'] = 'UNKNOWN'
                logging.warning(f" -> UNKNOWN: {vendor_name} (Error: {error_msg})")
        
        browser.close()
    
    # 5. Write the full, augmented dataset to the warehouse
    df.to_sql('fct_activation_ready', conn, if_exists='replace', index=False)
    conn.close()
    
    logging.info(f"Phase 2 Complete. {len(df)} records safely persisted to 'fct_activation_ready'.")

if __name__ == "__main__":
    run_signal_engineering()