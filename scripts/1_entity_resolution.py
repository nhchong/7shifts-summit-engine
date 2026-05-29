"""
Phase 1: Entity Resolution (Atomic Chain-of-Thought Layer)
-------------------------------------------------
This script performs high-precision entity resolution one record at a time.
"""

import sqlite3
import pandas as pd
import logging
import time
import os  # <--- THIS WAS MISSING
from pydantic import BaseModel, Field
from typing import List
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Configuration
# Configuration
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
# Suppress noisy HTTP logs from the Google GenAI/httpx libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)
load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DB_PATH = 'data/7shifts_nyc_data.db'

# Pydantic Schema
class EntityMatch(BaseModel):
    crm_company_id: str
    vendor_id: str
    confidence_score: float

class MatchList(BaseModel):
    matches: List[EntityMatch]

def run_pipeline():
    logging.info("Starting Atomic Phase 1: Resolving records individually")

    # 1. Extraction: Load the dbt-transformed table
    conn = sqlite3.connect(DB_PATH)
    master_df = pd.read_sql_query("SELECT * FROM fct_vendor_signals", conn)
    conn.close()

    unmatched_crm = master_df[master_df['gtm_segment'] == 'PENDING_RESOLUTION']
    unmatched_vendor = master_df[master_df['gtm_segment'] == 'NET_NEW_TARGET']
    
    logging.info(f"Records to resolve: {len(unmatched_crm)}")

    # 2. Probabilistic Resolution: Atomic processing (1 record at a time)
    for _, crm_record in unmatched_crm.iterrows():
        logging.info(f"Resolving: {crm_record['crm_name']}...")
        
        crm_payload = crm_record[['company_id', 'crm_name', 'primary_domain']].to_dict()
        vendor_payload = unmatched_vendor[['vendor_id', 'vendor_name', 'vendor_website']].to_dict('records')

        prompt = f"""
        Analyze the provided CRM record and determine if it matches any entity in the Vendor List.
        
        ### PROCESS:
        1. Compare Domain: Check if primary_domain matches vendor_website.
        2. Compare Normalized Name: Check if company names match after removing suffixes (LLC, Inc, Group, NYC, Restaurant).
        3. Exclusion: If no clear evidence exists, DO NOT GUESS.
        
        ### CONSTRAINTS:
        - If NO confident match exists, return an empty list for 'matches'.
        - Confidence Score must reflect certainty: 
            1.0: Domain match OR exact normalized name match.
            0.8: High-probability semantic match (e.g., Parent/Subsidiary relationship).
            <0.8: Reject.
        
        ### INPUT:
        CRM Record: {crm_payload}
        Candidate Vendor List: {vendor_payload}
        """

        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MatchList,
                    temperature=0.0,
                ),
            )
            
            llm_result = MatchList.model_validate_json(response.text)
            
            # Apply immediate patch if match found
            for match in llm_result.matches:
                if match.confidence_score >= 0.8:
                    crm_idx = master_df['company_id'] == match.crm_company_id
                    v_row = unmatched_vendor[unmatched_vendor['vendor_id'] == match.vendor_id].iloc[0]
                    
                    master_df.loc[crm_idx, ['vendor_id', 'vendor_name', 'vendor_website', 'global_location_count', 
                                           'total_nyc_locations', 'avg_nyc_rating', 'vendor_pos', 'cuisines']] = \
                        [v_row['vendor_id'], v_row['vendor_name'], v_row['vendor_website'], v_row['global_location_count'], 
                         v_row['total_nyc_locations'], v_row['avg_nyc_rating'], v_row['vendor_pos'], v_row['cuisines']]
                    
                    # Update whitespace and segment
                    global_locs = v_row['global_location_count'] if pd.notna(v_row['global_location_count']) else 0
                    crm_locs = master_df.loc[crm_idx, 'crm_location_count'].fillna(0).values[0]
                    master_df.loc[crm_idx, 'location_whitespace'] = global_locs - crm_locs
                    
                    # Recalculate segment
                    if master_df.loc[crm_idx, 'total_nyc_locations'].values[0] >= 3 and master_df.loc[crm_idx, 'avg_nyc_rating'].values[0] >= 4.0:
                        master_df.loc[crm_idx, 'gtm_segment'] = 'CREDIBLE_PARTNER'
                    else:
                        master_df.loc[crm_idx, 'gtm_segment'] = 'EXPANSION_OPPORTUNITY'
                        
                    logging.info(f" -> MAPPED: {crm_record['crm_name']} to {v_row['vendor_name']} (Score: {match.confidence_score})")
            
            # Atomic persist to warehouse
            conn = sqlite3.connect(DB_PATH)
            master_df.to_sql('fct_master_dataset', conn, if_exists='replace', index=False)
            conn.close()

        except Exception as e:
            logging.error(f"Failed to map {crm_record['crm_name']}: {e}")
            time.sleep(2) 

    logging.info("Phase 1 Complete. Dataset persisted atomically.")

if __name__ == "__main__":
    run_pipeline()