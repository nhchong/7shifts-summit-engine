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
    
    required_files = ['crm_accounts.xlsx', 'companies.xlsx', 'locations.xlsx']
    missing = [f for f in required_files if not os.path.exists(os.path.join(data_dir, f))]
    if missing:
        logging.error(f"Missing files: {missing}. Ensure all .xlsx files are in the data/ folder.")
        return

    try:
        logging.info("Reading Excel files into Pandas memory...")
        crm_df = pd.read_excel(os.path.join(data_dir, 'crm_accounts.xlsx'))
        companies_df = pd.read_excel(os.path.join(data_dir, 'companies.xlsx'))
        locations_df = pd.read_excel(os.path.join(data_dir, 'locations.xlsx'))

        logging.info("Writing DataFrames to SQLite tables...")
        crm_df.to_sql('crm_accounts', conn, if_exists='replace', index=False)
        companies_df.to_sql('companies', conn, if_exists='replace', index=False)
        locations_df.to_sql('locations', conn, if_exists='replace', index=False)

        conn.execute("VACUUM;")
        logging.info("Database vacuumed and optimized.")

        logging.info("Success! Local database '7shifts_nyc_data.db' has been built.")

    except Exception as e:
        logging.error(f"Ingestion failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    build_local_database()