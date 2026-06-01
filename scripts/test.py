import sqlite3
import pandas as pd
import os

DB_PATH = 'data/7shifts_nyc_data.db'
OUTPUT_PATH = 'outputs/fct_activation_ready_dump.csv'

def export_table():
    # Ensure output directory exists
    if not os.path.exists('outputs'):
        os.makedirs('outputs')

    conn = sqlite3.connect(DB_PATH)
    
    # Read the target table
    df = pd.read_sql_query("SELECT * FROM fct_activation_ready", conn)
    
    # Export to CSV
    df.to_csv(OUTPUT_PATH, index=False)
    conn.close()
    
    print(f"Successfully exported {len(df)} records to {OUTPUT_PATH}")

if __name__ == "__main__":
    export_table()