# Step 0: Wipe existing DB tables and ingest raw Excel data
python scripts/0_ingest_excel_to_db.py

# Step 1: Run your dbt models to build base layers and initial segmentation matrix
# (Ensure you switch to your dbt environment or directory if necessary)
cd seven_shifts_project
dbt run
cd ..

# Step 2: Resolve orphaned CRM records using RapidFuzz and Gemini AI
python scripts/1_entity_resolution.py

# Step 3: Run the Playwright headless browser to scrub for franchises
python scripts/2_signal_engineering.py

# Step 4: Boot up your refreshed Streamlit UI
streamlit run scripts/3_stakeholder_app.py