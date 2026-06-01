# 7shifts Summit GTM Engine

## 1. The Objective (Why)
Manual reconciliation of static CRM exports and fragmented vendor intelligence pollutes sales pipelines. Account Executives waste time on dead locations, enterprise franchises, and generic outreach. 

This engine treats sales outreach for the NYC Summit strictly as a data engineering problem. Leveraging frontline operations experience, it shifts GTM operations from passive dashboards to programmatic execution—automating entity resolution, franchise detection, dynamic segmentation, and AI-driven hyper-personalization.

## 2. The Architecture (What)
The system operates as a sequential pipeline, decoupling heavy data transformation from the lightweight presentation layer:

*   **Phase 0: Raw Ingestion (`0_ingest_excel_to_db.py`)** 
    Executes an idempotent batch load of static CRM and vendor Excel exports into a local SQLite database, establishing a standardized baseline schema.
*   **Phase 1: Entity Resolution (`1_entity_resolution.py`)** 
    Maps orphaned CRM accounts to scraped vendors to calculate exact addressable whitespace. Uses RapidFuzz for deterministic fast-path matching and Gemini 2.5 Flash (with strict JSON-schema enforcement) for complex brand architecture mapping.
*   **Phase 2: Signal Engineering (`2_signal_engineering.py`)** 
    Deploys Playwright (headless Chromium) with stealth configurations to hydrate Single Page Applications (SPAs) and execute strict regex heuristics, detecting franchises while bypassing modern WAFs/bot protection.
*   **Transformation Layer (dbt: `fct_vendor_signals.sql`)** 
    Calculates composite market influence scores via SQL window functions and routes entities into prioritized GTM tiers.
*   **Phase 3: Activation UI (`3_stakeholder_app.py`)** 
    A read-only Streamlit application deployed on Community Cloud. Allows GTM stakeholders to filter targets and trigger a two-pass AI loop, dynamically generating 3-sentence invitation drafts and internal rep battle cards.

## 3. The Segmentation Engine (How Targets Are Identified)
The system eliminates manual list-building by programmatically identifying the most valuable accounts through a two-step mathematical matrix.

### A. The Mathematical Ranking Layer
To prevent skewing by outliers (e.g., a massive footprint with terrible reviews, or a 5-star rating with only one location), the engine normalizes the data using statistical percentiles:
*   **Market Footprint Percentile:** Ranks physical density in the target market (0.0 to 1.0).
*   **Market Rating Percentile:** Ranks brand equity using a review-volume-weighted average rating (0.0 to 1.0).

These metrics are averaged to create a **Composite Influence Score**. A "top target" must possess both scale and institutional prestige.

### B. The Priority Routing Matrix
A deterministic `CASE WHEN` hierarchy routes the scored companies into specific GTM playbooks. Higher priority rules intercept records before lower rules evaluate them:

1.  **EXPANSION_OPPORTUNITY (Priority 1):** Existing customers with at least one un-deployed location (`location_whitespace >= 1`) or accounts stuck on basic low-tier plans. Focuses the Account Manager entirely on cross-sell revenue.
2.  **NET_NEW_TARGET (Priority 2):** Pure prospects landing in the **top 50%** of the Composite Influence Score. Eliminates low-value, single-unit SMBs from the outbound pipeline.
3.  **CREDIBLE_PARTNER (Priority 3):** Existing customers fully deployed across all their locations, but resting in the **top 20%** of the market influence index. Selected for VIP advocacy and social proof to influence prospects at the event.
4.  **UNCLASSIFIED / PENDING_RESOLUTION:** Low-tier prospects or broken records. Filtered completely out of the UI.

When GTM leaders input their desired pipeline ratios into the Streamlit UI, the Auto-Selection Engine applies a vectorized sort against these segments, mathematically guaranteeing that outbound efforts are concentrated on the highest-leverage accounts.

## 4. Tech Stack
*   **Core Data:** Python 3.10+, SQLite, Pandas, NumPy
*   **Transformation:** dbt (Data Build Tool) Core
*   **Entity Resolution & AI:** RapidFuzz, Google GenAI SDK (Gemini 2.5 Flash)
*   **Scraping:** Playwright, `playwright-stealth`
*   **Frontend/Export:** Streamlit, Openpyxl

## 5. Execution Protocol
To rebuild the pipeline locally:
1. Place raw Excel exports in `data/`.
2. Run `python scripts/0_ingest_excel_to_db.py` to build the database.
3. Execute `dbt run` within the `seven_shifts_project/` directory.
4. Run `python scripts/1_entity_resolution.py`.
5. Run `python scripts/2_signal_engineering.py`.
6. Run `streamlit run scripts/3_stakeholder_app.py` to launch the UI.