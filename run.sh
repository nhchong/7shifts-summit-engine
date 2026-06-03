#!/bin/bash
set -e

# Step 0: Wipe existing DB tables and ingest raw Excel data
python scripts/0_ingest_excel_to_db.py

# Step 1: Build base layers and initial segmentation matrix
cd seven_shifts_project
dbt run
cd ..

# Step 2: Resolve orphaned CRM records using RapidFuzz and Gemini AI
# Outputs entity_resolution_map and duplicate_vendor_ids tables
python scripts/1_entity_resolution.py

# Step 3: Apply entity resolution results and recalculate final segments in SQL
cd seven_shifts_project
dbt run
cd ..

# Step 4: Run Playwright headless browser to scrub for franchises
python scripts/2_signal_engineering.py

# Step 5: Boot up the Streamlit UI
streamlit run scripts/3_stakeholder_app.py
