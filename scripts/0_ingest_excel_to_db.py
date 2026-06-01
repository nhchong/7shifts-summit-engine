"""
Phase 0: Raw Data Ingestion Layer
---------------------------------
Bridges raw, manually-exported stakeholder data (Excel) into a localized, 
queryable SQLite database. This serves as the foundational state for 
downstream dbt transformations and the final Streamlit UI.
"""

import sqlite3
import pandas as pd
import logging
import os

# Configure stdout logging for pipeline orchestration visibility
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def build_local_database():
    """
    Executes an idempotent batch load of static Excel files into SQLite.
    Fails safely if upstream dependencies (files) are missing.
    """
    logging.info("Starting Step 0: Ingesting Excel files to SQLite...")
    
    # Establish execution context. Hardcoded to 'data/' assuming invocation from repository root.
    data_dir = 'data/'
    db_path = os.path.join(data_dir, '7shifts_nyc_data.db')
    
    # Initialize connection. Creates the .db file if it does not exist.
    conn = sqlite3.connect(db_path)
    
    try:
        logging.info("Reading Excel files into Pandas memory...")
        # Target schemas: CRM state, vendor intelligence, and physical footprint data
        crm_df = pd.read_excel(os.path.join(data_dir, 'crm_accounts.xlsx'))
        companies_df = pd.read_excel(os.path.join(data_dir, 'companies.xlsx'))
        locations_df = pd.read_excel(os.path.join(data_dir, 'locations.xlsx'))
        
        logging.info("Writing DataFrames to SQLite tables...")
        # if_exists='replace' guarantees pipeline idempotency. Rerunning Step 0 wipes dirty state.
        # index=False prevents Pandas from writing the arbitrary dataframe index as a primary key.
        crm_df.to_sql('crm_accounts', conn, if_exists='replace', index=False)
        companies_df.to_sql('companies', conn, if_exists='replace', index=False)
        locations_df.to_sql('locations', conn, if_exists='replace', index=False)
        
        # Reclaim unused disk space from replaced tables and optimize index trees for dbt execution
        conn.execute("VACUUM;")
        logging.info("Database vacuumed and optimized.")
        
        logging.info("Success! Local database '7shifts_nyc_data.db' has been built.")
        
    except FileNotFoundError as e:
        # Catch and surface upstream human error (missing exports) before downstream pipeline executes
        logging.error(f"Missing file: {e}. Ensure all .xlsx files are in the data/ folder.")
    finally:
        # Prevent database locking, allowing dbt to safely acquire a read connection in Phase 1
        conn.close()

if __name__ == "__main__":
    build_local_database()