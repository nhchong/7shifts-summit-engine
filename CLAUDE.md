# CLAUDE.md — 7shifts Summit GTM Engine

## What This Is
A data engineering pipeline built as a portfolio project for a GTM Engineer role at 7shifts. It automates target selection and outreach for an exclusive NYC restaurant summit — entity resolution, franchise detection, composite scoring, and a Streamlit activation UI. The full brief is in `case-study-brief.md`.

---

## Pipeline Execution Order
The pipeline is strictly sequential. Each step depends on the previous one's database output.

```
Step 0: python scripts/0_ingest_excel_to_db.py          # Excel → SQLite raw tables
Step 1: cd seven_shifts_project && dbt run               # Raw tables → fct_vendor_signals
Step 2: cd .. && python scripts/1_entity_resolution.py   # fct_vendor_signals → entity_resolution_map + duplicate_vendor_ids
Step 3: cd seven_shifts_project && dbt run               # Mapping tables → fct_master_dataset
Step 4: cd .. && python scripts/2_signal_engineering.py  # fct_master_dataset → fct_activation_ready
Step 5: streamlit run scripts/3_stakeholder_app.py       # Reads fct_activation_ready
```

**Always run from the project root** (`~/Projects/7shifts_summit_project`), except dbt which must be run from inside `seven_shifts_project/`.

---

## Database Tables
All tables live in `data/7shifts_nyc_data.db`.

| Table | Created By | Purpose |
|---|---|---|
| `crm_accounts` | Phase 0 | Raw Salesforce export of existing 7shifts NYC customers |
| `companies` | Phase 0 | Vendor list: 500+ restaurant groups with location counts and POS |
| `locations` | Phase 0 | One row per physical location — cuisine, rating, price tier, Instagram |
| `fct_vendor_signals` | dbt (Step 1) | Joins companies + locations, calculates composite influence score and initial GTM tier routing |
| `entity_resolution_map` | Phase 1 | Maps PENDING_RESOLUTION CRM records to their matched vendor IDs |
| `duplicate_vendor_ids` | Phase 1 | Vendor IDs flagged as scraper-generated clones to exclude |
| `fct_master_dataset` | dbt (Step 3) | Applies entity resolution, recalculates EPI and final GTM segments in SQL |
| `fct_activation_ready` | Phase 2 | Adds franchise flags from web scraping; final table for the UI |

---

## Environment
Requires a `.env` file in the project root with:
```
GEMINI_API_KEY=your_key_here
```
Used by Phase 1 (entity resolution fallback) and Phase 3 (invitation draft generation). Model: `gemini-2.5-flash`.

---

## Key Design Decisions

**Entity Resolution (Phase 1 + dbt Step 3)**
- Phase 1 (Python) only does what SQL can't: fuzzy matching + LLM reasoning
- Outputs two mapping tables: `entity_resolution_map` and `duplicate_vendor_ids`
- dbt Step 3 consumes those tables to recalculate EPI and segments in SQL — business logic stays in one place
- Two-tier matching: RapidFuzz fast-path (threshold: 95) → Gemini fallback for complex brand architectures
- Pre-filters top 10 fuzzy candidates before LLM call to minimize token cost

**Franchise Detection (Phase 2)**
- Only scrapes companies with `global_location_count >= 10` (high-risk filter)
- Uses Playwright + playwright-stealth to bypass SPAs and WAFs
- Tri-state result: `True` / `False` / `UNKNOWN` (timeout or blocked)
- Records blocked as `UNKNOWN` are kept in the pipeline, not dropped

**Segmentation Logic**
Segments are assigned in dbt (`fct_master_dataset`). Initial routing happens in `fct_vendor_signals`; final routing after entity resolution happens in `fct_master_dataset`:
- `EXPANSION_OPPORTUNITY` — existing customer with whitespace or low-tier plan (EPI > 0)
- `NET_NEW_TARGET` — prospect in top 50% composite influence score
- `CREDIBLE_PARTNER` — fully deployed customer in top 20% market influence
- `PENDING_RESOLUTION` — CRM record not yet matched to a vendor
- `UNCLASSIFIED` — filtered out of the UI entirely

**Streamlit UI (Phase 3)**
- Auto-Selection Engine: sidebar lets user define segment breakdown %, auto-selects top N targets per segment sorted by EPI
- Two-pass AI loop: Generate baseline drafts → Rep adds notes → Regenerate hyper-personalized copy
- Export: dual-sheet Excel (Existing Customers / Net-New Prospects) per Jamie's Salesforce spec

---

## Gotchas
- Phase 2 (scraping) is slow — ~20s timeout per site, only runs on high-risk vendors
- `streamlit run` must be called from project root so the relative `data/` path resolves correctly
- dbt runs twice: Step 1 builds `fct_vendor_signals` (needed by Phase 1), Step 3 builds `fct_master_dataset` (needed by Phase 2)
- If the database is rebuilt (Phase 0), both dbt runs must be re-executed
- The `.env` file is gitignored — don't commit it
