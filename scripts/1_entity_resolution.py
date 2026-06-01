"""
Phase 1: Entity Resolution Engine
-------------------------------------------------
Maps orphaned CRM accounts to scraped vendor intelligence.
Executes a hybrid deterministic/probabilistic DAG to ensure pipeline fidelity:
1. Deduplication: Prunes scraper-generated clones to prevent cross-contamination.
2. Deterministic Fast-Path: RapidFuzz token sorting for low-latency, high-confidence matches.
3. Probabilistic Fallback: Gemini LLM with enforced JSON schema for complex brand architectures.
"""

import sqlite3
import pandas as pd
import numpy as np
import logging
import time
import os
import re
from rapidfuzz import fuzz
from pydantic import BaseModel, Field
from typing import Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

# --- Configuration & Observability ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
# Suppress noisy dependency logs to maintain orchestrator visibility
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DB_PATH = 'data/7shifts_nyc_data.db'

# 95 is a strict threshold to prevent false positives on generic names (e.g., "Joe's Pizza" vs "Joe's Deli")
MATCH_THRESHOLD = 95

# --- String Normalization ---
# Compiles regex once at runtime. Strips corporate legal entities and geographic noise 
# to expose the core brand token for fuzzy matching.
NOISE_PATTERN = re.compile(
    r'\b(' + '|'.join([
        r'restaurant group', r'hospitality group', r'hospitality',
        r'restaurants', r'restaurant', r'group', r'llc', r'inc',
        r'incorporated', r'corporation', r'corp', r'company', r'co',
        r'nyc', r'new york', r'operations', r'brasserie', r'cafe',
        r'kitchen', r'bakery', r'deli', r'grocery'
    ]) + r')\b', 
    re.IGNORECASE
)

def clean_entity_name(name: str) -> str:
    """Standardizes entity names by stripping punctuation, corporate suffixes, and excess whitespace."""
    if not isinstance(name, str): return ""
    clean_name = re.sub(r'[^\w\s]', '', name.lower())
    clean_name = NOISE_PATTERN.sub('', clean_name)
    return re.sub(r'\s+', ' ', clean_name).strip()

# --- AI Schema Contract ---
# Enforces strict deterministic output from the LLM, preventing unstructured hallucinations.
class AIResolution(BaseModel):
    reasoning: str = Field(description="Step-by-step logic comparing domain and core brand name.")
    is_match: bool
    matched_vendor_id: Optional[str] = Field(None, description="The vendor_id if a match is found.")
    confidence_score: float

# --- State Mutation ---
def apply_match(df, crm_record, v_row, confidence):
    """
    Mutates the master dataframe in-memory to merge vendor intelligence into the CRM record.
    Re-evaluates the mathematical segmentation logic originally defined in dbt.
    """
    crm_idx = df['company_id'] == crm_record['company_id']
    
    # 1. Hydrate the CRM row with vendor attributes and pre-calculated percentiles
    vendor_cols = [
        'vendor_id', 'vendor_name', 'vendor_website', 'global_location_count', 
        'total_nyc_locations', 'avg_nyc_rating', 'vendor_pos', 'cuisines', 'price_tier',
        'market_footprint_percentile', 'market_rating_percentile', 'composite_influence_score'
    ]
    
    # Safely inject values, ignoring missing columns
    for col in vendor_cols:
        if col in v_row:
            df.loc[crm_idx, col] = v_row[col]
    
    # 2. Recalculate Addressable Whitespace
    global_locs = v_row['global_location_count'] if pd.notna(v_row['global_location_count']) else 0
    crm_locs = df.loc[crm_idx, 'crm_location_count'].fillna(0).values[0]
    whitespace = global_locs - crm_locs
    df.loc[crm_idx, 'location_whitespace'] = whitespace
    
    # 3. Dynamic Segment Logic: Synchronized with downstream dbt model
    crm_plan = str(df.loc[crm_idx, 'crm_plan'].values[0])
    price_tier = str(v_row.get('price_tier', ''))
    
    # Calculate ACV Proxy
    if price_tier == '$$$$':
        acv_proxy = 4.0
    elif price_tier == '$$$':
        acv_proxy = 3.0
    elif price_tier == '$$':
        acv_proxy = 2.0
    else:
        acv_proxy = 1.0
        
    # Calculate Plan Upgrade Delta
    if crm_plan in ['Comp', 'Free']:
        plan_upgrade_delta = 3.0
    elif crm_plan == 'Essentials':
        plan_upgrade_delta = 2.0
    else:
        plan_upgrade_delta = 0.0
        
    # Calculate EPI
    epi = (whitespace * acv_proxy) + (crm_locs * plan_upgrade_delta)
    df.loc[crm_idx, 'expansion_potential_index'] = epi
    
    # Apply Segment Routing
    comp_score = v_row['composite_influence_score'] if pd.notna(v_row['composite_influence_score']) else 0.0
    
    if epi > 0:
        df.loc[crm_idx, 'gtm_segment'] = 'EXPANSION_OPPORTUNITY'
    elif whitespace <= 0 and comp_score >= 0.8:
        df.loc[crm_idx, 'gtm_segment'] = 'CREDIBLE_PARTNER'
    else:
        df.loc[crm_idx, 'gtm_segment'] = 'UNCLASSIFIED'

def run_pipeline():
    logging.info("Starting Phase 1: RapidFuzz + AI Resolution")

    # Load the baseline state generated by the dbt transformation layer
    conn = sqlite3.connect(DB_PATH)
    master_df = pd.read_sql_query("SELECT * FROM fct_vendor_signals", conn)
    conn.close()

    # ==========================================
    # STEP 1: VENDOR-TO-VENDOR DEDUPLICATION
    # ==========================================
    # Scrapers often generate multiple vendor IDs for the same business (e.g., DBA variations).
    # We must drop unmatched clones that conflict with correctly mapped vendor records.
    logging.info("Running Vendor-to-Vendor Deduplication pass...")
    
    matched_vendors = master_df[master_df['company_id'].notna() & master_df['vendor_id'].notna()]
    unmatched_prospects = master_df[master_df['company_id'].isna() & master_df['vendor_id'].notna()]
    
    indices_to_drop = []

    for idx, prospect in unmatched_prospects.iterrows():
        p_clean = clean_entity_name(str(prospect['vendor_name']))
        if not p_clean: continue
            
        for _, matched_v in matched_vendors.iterrows():
            m_clean = clean_entity_name(str(matched_v['vendor_name']))
            
            # token_sort_ratio ignores word order, matching "Pizza Joes" to "Joes Pizza"
            if fuzz.token_sort_ratio(p_clean, m_clean) >= MATCH_THRESHOLD:
                logging.info(f" -> DUPLICATE VENDOR FOUND: '{prospect['vendor_name']}' is a clone of '{matched_v['vendor_name']}'. Tagging for removal.")
                indices_to_drop.append(idx)
                break
                
    if indices_to_drop:
        master_df = master_df.drop(indices_to_drop)
        logging.info(f"Dropped {len(indices_to_drop)} duplicate vendor records.")

    # ==========================================
    # STEP 2: ORPHANED CRM RESOLUTION
    # ==========================================
    unmatched_crm = master_df[master_df['gtm_segment'] == 'PENDING_RESOLUTION']
    unmatched_vendor = master_df[master_df['vendor_id'].notna() & master_df['company_id'].isna()]
    
    resolved_ghost_vendors = []
    
    logging.info(f"Records to resolve: {len(unmatched_crm)}")

    for _, crm_record in unmatched_crm.iterrows():
        matched = False
        crm_clean = clean_entity_name(str(crm_record['crm_name']))
        
        if not crm_clean:
            continue
        
        # TIER 1: RAPIDFUZZ FAST-PATH
        # Bypasses expensive LLM calls for mathematically obvious matches.
        for _, v_row in unmatched_vendor.iterrows():
            v_clean = clean_entity_name(str(v_row['vendor_name']))
            score = fuzz.token_sort_ratio(crm_clean, v_clean)
            
            if score >= MATCH_THRESHOLD:
                logging.info(f" -> FAST-MATCH: {crm_record['crm_name']} to {v_row['vendor_name']} (Score: {score})")
                apply_match(master_df, crm_record, v_row, score / 100)
                resolved_ghost_vendors.append(v_row['vendor_id'])
                matched = True
                break
        
        if matched: continue

        # TIER 2: AI REASONING FALLBACK
        # For complex corporate structures (e.g., CRM="Symphony Ops", Vendor="The Blue Room")
        logging.info(f"Resolving with AI: {crm_record['crm_name']}...")
        crm_payload = crm_record[['company_id', 'crm_name', 'primary_domain']].to_dict()
        
        # Performance/Cost Optimization: Pre-filter the universe of vendors down to the top 10 
        # algorithmic candidates before injecting into the LLM context window.
        top_candidates = unmatched_vendor.copy()
        top_candidates['temp_score'] = top_candidates['vendor_name'].apply(
            lambda x: fuzz.token_sort_ratio(crm_clean, clean_entity_name(str(x)))
        )
        best_vendors = top_candidates.nlargest(10, 'temp_score')[['vendor_id', 'vendor_name', 'vendor_website']].to_dict('records')

        prompt = f"""
        You are a Data Intelligence Analyst performing strict entity resolution. 
        Determine if the 'CRM Record' represents the exact same business entity as ANY of the 'Candidate Vendors'.

        ### REASONING DIRECTIVES:
        1. THE DOMAIN CHECK (PRIMARY): Compare 'primary_domain' and 'vendor_website'. 
           - Exact matches on proprietary domains are a guaranteed match.
           - Ignore generic email providers (gmail.com, yahoo.com). 
           - If domains do not match, or if either domain is missing/null, DO NOT immediately disqualify. Proceed to Step 2.
        
        2. NAME NORMALIZATION (SECONDARY): Strip all corporate suffixes (LLC, Inc, Group, Hospitality, Operations) and geographic markers (NYC, Brooklyn, New York) from both names.
        
        3. CORE BRAND EXTRACTION: After stripping noise, compare the core brand identities.
           - Be aggressive in identifying core brands despite venue-type suffixes. These should be considered matches if there are no conflicting domains.
           
        4. FALSE FRIENDS: Do not match entities just because they share a generic industry word (e.g., "The Italian Restaurant" does not match "The Italian Kitchen").

        ### EXECUTION STEPS:
        Step 1: Write down your step-by-step logic in the 'reasoning' field. Evaluate the domains first. If that is inconclusive, extract and compare the core brand names.
        Step 2: Assign a 'confidence_score' between 0.0 and 1.0 based on your analysis.
        Step 3: If and ONLY IF the confidence is 0.8 or higher, set 'is_match' to true and provide the 'matched_vendor_id'. Otherwise, set 'is_match' to false.

        ### INPUT:
        CRM Record: {crm_payload}
        Candidate Vendors: {best_vendors}
        """

        try:
            # temperature=0.0 enforces deterministic evaluation routing
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", 
                    response_schema=AIResolution, 
                    temperature=0.0
                ),
            )
            
            result = AIResolution.model_validate_json(response.text)
            
            if result.is_match and result.matched_vendor_id:
                v_row = unmatched_vendor[unmatched_vendor['vendor_id'] == result.matched_vendor_id].iloc[0]
                apply_match(master_df, crm_record, v_row, result.confidence_score)
                resolved_ghost_vendors.append(result.matched_vendor_id)
                logging.info(f" -> MAPPED: {crm_record['crm_name']} to {v_row['vendor_name']} (Reason: {result.reasoning})")
            else:
                logging.info(f" -> NO MATCH FOUND for {crm_record['crm_name']}")
        
        except Exception as e:
            logging.error(f"Failed to map {crm_record['crm_name']}: {e}")
            time.sleep(2) # Backoff to prevent rate-limiting cascade

    # ==========================================
    # STEP 3: PERSISTENCE
    # ==========================================
    if resolved_ghost_vendors:
        # Drop the original vendor rows that lack a company_id but were successfully mapped
        ghost_mask = (master_df['vendor_id'].isin(resolved_ghost_vendors)) & (master_df['company_id'].isna())
        master_df = master_df[~ghost_mask]
        logging.info(f"Purged {len(resolved_ghost_vendors)} ghost vendor records.")
        
    logging.info("Writing finalized dataset to database...")
    conn = sqlite3.connect(DB_PATH)
    # Atomic transaction: over-writes the intermediate table with the fully resolved schema
    master_df.to_sql('fct_master_dataset', conn, if_exists='replace', index=False)
    conn.close()
    
    logging.info("Phase 1 Complete. Dataset persisted atomically.")

if __name__ == "__main__":
    run_pipeline()