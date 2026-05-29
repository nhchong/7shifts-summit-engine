import sqlite3
import pandas as pd
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def build_local_database():
    logging.info("Starting Step 0: Ingesting Excel files to SQLite...")
    
    # Path is relative to the terminal execution directory
    data_dir = 'data/'
    db_path = os.path.join(data_dir, '7shifts_nyc_data.db')
    
    # 1. Create a connection to a new local SQLite database
    conn = sqlite3.connect(db_path)
    
    try:
        # 2. Read the Excel files
        logging.info("Reading Excel files...")
        crm_df = pd.read_excel(os.path.join(data_dir, 'crm_accounts.xlsx'))
        companies_df = pd.read_excel(os.path.join(data_dir, 'companies.xlsx'))
        locations_df = pd.read_excel(os.path.join(data_dir, 'locations.xlsx'))
        
        # 3. Write them to SQL tables
        logging.info("Writing DataFrames to SQLite tables...")
        crm_df.to_sql('crm_accounts', conn, if_exists='replace', index=False)
        companies_df.to_sql('companies', conn, if_exists='replace', index=False)
        locations_df.to_sql('locations', conn, if_exists='replace', index=False)
        
        # 4. Optimize the database after loading to ensure dbt reads clean structures
        conn.execute("VACUUM;")
        logging.info("Database vacuumed and optimized.")
        
        logging.info("Success! Local database '7shifts_nyc_data.db' has been built.")
        
    except FileNotFoundError as e:
        logging.error(f"Missing file: {e}. Ensure all .xlsx files are in the data/ folder.")
    finally:
        conn.close()

if __name__ == "__main__":
    build_local_database()