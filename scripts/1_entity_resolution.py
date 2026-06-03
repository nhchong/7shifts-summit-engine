"""
Phase 1: Entity Resolution Engine
-------------------------------------------------
Maps orphaned CRM accounts to scraped vendor intelligence.
Executes a hybrid deterministic/probabilistic DAG to ensure pipeline fidelity:
1. Deduplication: Prunes scraper-generated clones to prevent cross-contamination.
2. Deterministic Fast-Path: RapidFuzz token sorting for low-latency, high-confidence matches.
3. Probabilistic Fallback: Gemini LLM with enforced JSON schema for complex brand architectures.

Outputs two mapping tables consumed by the subsequent dbt run to build fct_master_dataset:
  - entity_resolution_map: (company_id → matched_vendor_id) pairs
  - duplicate_vendor_ids:  vendor IDs flagged as scraper-generated clones
"""

import sqlite3
import pandas as pd
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

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)

load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DB_PATH = 'data/7shifts_nyc_data.db'

# 95 is a strict threshold to prevent false positives on generic names (e.g., "Joe's Pizza" vs "Joe's Deli")
MATCH_THRESHOLD = 95

NOISE_PATTERN = re.compile(
    r'\b(' + '|'.join([
        r'restaurant group', r'hospitality group', r'hospitality',
        r'restaurants', r'restaurant', r'group', r'llc', r'inc',
        r'incorporated', r'corporation', r'corp', r'company', r'co',
        r'nyc', r'new york', r'operations'
    ]) + r')\b',
    re.IGNORECASE
)

def clean_entity_name(name: str) -> str:
    if not isinstance(name, str): return ""
    clean_name = re.sub(r'[^\w\s]', '', name.lower())
    clean_name = NOISE_PATTERN.sub('', clean_name)
    return re.sub(r'\s+', ' ', clean_name).strip()

class AIResolution(BaseModel):
    reasoning: str = Field(description="Step-by-step logic comparing domain and core brand name.")
    is_match: bool
    matched_vendor_id: Optional[str] = Field(None, description="The vendor_id if a match is found.")
    confidence_score: float

def run_pipeline():
    logging.info("Starting Phase 1: Entity Resolution")

    conn = sqlite3.connect(DB_PATH)
    try:
        master_df = pd.read_sql_query("SELECT * FROM fct_vendor_signals", conn)

        # ==========================================
        # STEP 1: VENDOR-TO-VENDOR DEDUPLICATION
        # ==========================================
        logging.info("Running Vendor-to-Vendor Deduplication pass...")

        matched_vendors = master_df[master_df['company_id'].notna() & master_df['vendor_id'].notna()]
        unmatched_prospects = master_df[master_df['company_id'].isna() & master_df['vendor_id'].notna()]

        duplicate_vendor_ids = []

        for _, prospect in unmatched_prospects.iterrows():
            p_clean = clean_entity_name(str(prospect['vendor_name']))
            if not p_clean: continue

            for _, matched_v in matched_vendors.iterrows():
                m_clean = clean_entity_name(str(matched_v['vendor_name']))
                if fuzz.token_sort_ratio(p_clean, m_clean) >= MATCH_THRESHOLD:
                    logging.info(f" -> DUPLICATE: '{prospect['vendor_name']}' is a clone of '{matched_v['vendor_name']}'")
                    duplicate_vendor_ids.append(prospect['vendor_id'])
                    break

        logging.info(f"Found {len(duplicate_vendor_ids)} duplicate vendor records.")

        # ==========================================
        # STEP 2: ORPHANED CRM RESOLUTION
        # ==========================================
        unmatched_crm = master_df[master_df['gtm_segment'] == 'PENDING_RESOLUTION']
        unmatched_vendor = master_df[
            master_df['vendor_id'].notna() &
            master_df['company_id'].isna() &
            ~master_df['vendor_id'].isin(duplicate_vendor_ids)
        ]

        resolution_records = []

        logging.info(f"Records to resolve: {len(unmatched_crm)}")

        for _, crm_record in unmatched_crm.iterrows():
            matched = False
            crm_clean = clean_entity_name(str(crm_record['crm_name']))

            if not crm_clean:
                continue

            # TIER 1: RAPIDFUZZ FAST-PATH
            for _, v_row in unmatched_vendor.iterrows():
                v_clean = clean_entity_name(str(v_row['vendor_name']))
                score = fuzz.token_sort_ratio(crm_clean, v_clean)

                if score >= MATCH_THRESHOLD:
                    logging.info(f" -> FAST-MATCH: {crm_record['crm_name']} to {v_row['vendor_name']} (Score: {score})")
                    resolution_records.append({
                        'company_id': crm_record['company_id'],
                        'matched_vendor_id': v_row['vendor_id'],
                        'match_method': 'RAPIDFUZZ',
                        'confidence_score': round(score / 100, 2)
                    })
                    unmatched_vendor = unmatched_vendor[unmatched_vendor['vendor_id'] != v_row['vendor_id']]
                    matched = True
                    break

            if matched: continue

            # TIER 2: AI REASONING FALLBACK
            logging.info(f"Resolving with AI: {crm_record['crm_name']}...")
            crm_payload = crm_record[['company_id', 'crm_name', 'primary_domain']].to_dict()

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
                    v_row = unmatched_vendor[unmatched_vendor['vendor_id'] == result.matched_vendor_id]
                    vendor_name = v_row['vendor_name'].iloc[0] if not v_row.empty else result.matched_vendor_id
                    resolution_records.append({
                        'company_id': crm_record['company_id'],
                        'matched_vendor_id': result.matched_vendor_id,
                        'match_method': 'AI',
                        'confidence_score': result.confidence_score
                    })
                    unmatched_vendor = unmatched_vendor[unmatched_vendor['vendor_id'] != result.matched_vendor_id]
                    logging.info(f" -> MAPPED: {crm_record['crm_name']} to {vendor_name} (Reason: {result.reasoning})")
                else:
                    logging.info(f" -> NO MATCH FOUND for {crm_record['crm_name']}")

            except Exception as e:
                logging.error(f"Failed to map {crm_record['crm_name']}: {e}")
                time.sleep(2)

        # ==========================================
        # STEP 3: PERSISTENCE
        # ==========================================
        logging.info("Writing resolution mapping tables to database...")

        pd.DataFrame({'vendor_id': duplicate_vendor_ids}).to_sql(
            'duplicate_vendor_ids', conn, if_exists='replace', index=False
        )

        pd.DataFrame(
            resolution_records if resolution_records else [],
            columns=['company_id', 'matched_vendor_id', 'match_method', 'confidence_score']
        ).to_sql('entity_resolution_map', conn, if_exists='replace', index=False)

        logging.info(f"Phase 1 Complete. {len(resolution_records)} matches recorded, {len(duplicate_vendor_ids)} duplicates flagged.")
        logging.info("Next: run 'dbt run' inside seven_shifts_project/ to materialize fct_master_dataset.")

    finally:
        conn.close()

if __name__ == "__main__":
    run_pipeline()
