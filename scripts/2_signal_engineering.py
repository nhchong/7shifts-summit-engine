"""
Phase 2: Signal Engineering & Activation Scrubbing
--------------------------------------------------
Executes targeted external enrichment to detect franchise architectures.
Standard HTTP requests (e.g., Requests, BeautifulSoup) fail on modern restaurant 
websites built as Single Page Applications (SPAs). This script deploys a headless 
Chromium browser to fully render JavaScript DOMs and execute heuristic checks, 
outputting the final 'fct_activation_ready' table for GTM consumption.
"""

import pandas as pd
import logging
import sqlite3
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_sync

# --- Observability ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DB_PATH = 'data/7shifts_nyc_data.db'

# --- Heuristic Signatures ---
# Strict regex anchors (\b) prevent false positives from press articles 
# (e.g., avoiding hits on "the franchise tag in the NFL").
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
    
    # 1. Acquire State: Pull the resolved output from Phase 1
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM fct_master_dataset", conn)
    
    # 2. Schema Evolution: Support graceful degradation (Tri-state Boolean)
    # Using 'object' dtype allows True, False, or 'UNKNOWN' if a site blocks the scraper.
    df['is_franchise'] = pd.Series(dtype='object')
    df['is_franchise'] = False 
    df['franchise_evidence'] = None
    df['scrape_status'] = 'UNATTEMPTED'
    
    # 3. Compute Optimization: Filter the scrape queue.
    # Scraping mom-and-pop shops is a waste of compute. We only target "High-Risk" 
    # entities mathematically capable of being large franchises.
    mask = (df['global_location_count'] >= 10) & (df['vendor_website'].notna()) & (df['vendor_website'] != '')
    high_risk_indices = df[mask].index
    
    logging.info(f"Detected {len(high_risk_indices)} high-risk vendors for deep-scrape.")
    
    # 4. Asynchronous Network Execution (Synchronous wrapper for local execution)
    with sync_playwright() as p:
        # Launch Chromium. Context isolates cookies and sets a normalized user-agent.
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        for idx in high_risk_indices:
            vendor_name = df.at[idx, 'vendor_name']
            raw_url = df.at[idx, 'vendor_website']
            
            # Normalize schema for the browser
            target_url = raw_url if raw_url.startswith(('http://', 'https://')) else f"https://{raw_url}"
            logging.info(f"Checking {vendor_name} ({target_url})...")
            
            try:
                # WAF BYPASS: Injects stealth scripts to mask WebDriver properties, 
                # preventing automatic blocks from Cloudflare/Akamai.
                stealth_sync(page)
                
                # PERFORMANCE: 'domcontentloaded' avoids infinite hangs caused by 
                # broken 3rd-party tracking pixels waiting for 'networkidle'.
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
                    
                # Hydration Extraction: Pulls the physical text actually rendered to the user
                body_text = page.inner_text('body')
                
                # Business Logic: Evaluate the payload
                match = FRANCHISE_PATTERN.search(body_text)
                
                if match:
                    df.at[idx, 'is_franchise'] = True
                    df.at[idx, 'franchise_evidence'] = match.group(0)
                    logging.info(f" -> FRANCHISE DETECTED: {vendor_name} (Evidence: '{match.group(0)}')")
                else:
                    df.at[idx, 'is_franchise'] = False
                    logging.info(f" -> PASS: {vendor_name} verified as independent.")
                    
            except PlaywrightTimeoutError:
                # Graceful Failure: Assign 'UNKNOWN' rather than dropping the record
                df.at[idx, 'scrape_status'] = 'TIMEOUT'
                df.at[idx, 'is_franchise'] = 'UNKNOWN'
                logging.warning(f" -> UNKNOWN: {vendor_name} (Timeout - Site too slow or blocked bot)")
            except Exception as e:
                # Network Layer Drops: Extract the Chromium net::ERR code (e.g., DNS resolution failure)
                error_msg = str(e).split('\n')[0] 
                df.at[idx, 'scrape_status'] = f"ERROR: {error_msg}"
                df.at[idx, 'is_franchise'] = 'UNKNOWN'
                logging.warning(f" -> UNKNOWN: {vendor_name} (Error: {error_msg})")
        
        browser.close()
    
    # 5. Pipeline Terminal Persistence
    # Atomic transaction to provision the final materialized view for the Streamlit UI.
    df.to_sql('fct_activation_ready', conn, if_exists='replace', index=False)
    conn.close()
    
    logging.info(f"Phase 2 Complete. {len(df)} records safely persisted to 'fct_activation_ready'.")

if __name__ == "__main__":
    run_signal_engineering()