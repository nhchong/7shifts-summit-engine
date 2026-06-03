"""
Phase 3: GTM Stakeholder UI & Activation Layer
----------------------------------------------
Serves as the final presentation layer. This Streamlit application allows the
Go-To-Market (GTM) team to interactively filter the enriched data, trigger
LLM-based personalized outreach drafting, and export the finalized pipeline
into a dual-sheet Excel format for activation.
"""

import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import io
import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# --- Initialization ---
st.set_page_config(page_title="7shifts Summit Selector", layout="wide")
load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SEGMENT_LABELS = {
    'NET_NEW_TARGET':        '🎯 Net New Target',
    'EXPANSION_OPPORTUNITY': '📈 Expansion Opportunity',
    'CREDIBLE_PARTNER':      '⭐ Credible Partner',
    'PENDING_RESOLUTION':    '⏳ Pending Resolution',
    'UNCLASSIFIED':          'Unclassified',
}

@st.cache_data
def load_data():
    conn = sqlite3.connect('data/7shifts_nyc_data.db')
    df = pd.read_sql_query("SELECT * FROM fct_activation_ready", conn)
    conn.close()

    df['is_franchise_str'] = df['is_franchise'].astype(str).str.strip().str.upper()
    filtered = df[df['is_franchise_str'].isin(['0', '0.0', 'FALSE', 'UNKNOWN'])].copy()
    filtered.drop(columns=['is_franchise_str'], inplace=True)
    return filtered

def generate_messaging_and_context(selected_df):
    drafts = []
    contexts = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, (_, row) in enumerate(selected_df.iterrows()):
        name = row['crm_name'] if pd.notna(row['crm_name']) and row['crm_name'] != '' else row['vendor_name']
        cuisine = row['cuisines'] if pd.notna(row['cuisines']) else 'restaurant'
        segment = row['gtm_segment'] if pd.notna(row['gtm_segment']) else 'Unknown Segment'

        loc_count = int(row['total_nyc_locations']) if pd.notna(row['total_nyc_locations']) else 'multiple'
        global_locs = int(row['global_location_count']) if pd.notna(row['global_location_count']) else 'unknown'
        rating = row['avg_nyc_rating'] if pd.notna(row['avg_nyc_rating']) else 'highly-rated'
        pos = row['vendor_pos'] if pd.notna(row['vendor_pos']) and str(row['vendor_pos']).strip() != '' else 'Unknown'

        rep_notes = row.get('rep_added_context', '')
        rep_notes_instruction = f"\nCRITICAL REP NOTES TO INTEGRATE: {rep_notes}" if str(rep_notes).strip() != '' else ""

        price = row.get('price_tier', 'premium')
        ig_url = row.get('instagram_url', '')
        has_ig = "Yes" if str(ig_url).strip() != '' and str(ig_url) != 'nan' else "No"

        prompt = f"""
        You are a Go-To-Market strategist generating a targeted event invitation for 7shifts, a restaurant team management platform.

        TARGET COMPANY DATA:
        Company: {name}
        GTM Segment: {segment}
        NYC Locations: {loc_count}
        Global Locations: {global_locs}
        POS System: {pos}
        Cuisine: {cuisine}
        Average Rating: {rating}
        Price Tier: {price}
        Active on Instagram: {has_ig}{rep_notes_instruction}

        COPYWRITING RULES FOR THE INVITATION DRAFT:
        1. THE PRIMARY OBJECTIVE: You must explicitly invite them to an exclusive, invite-only VIP Dinner and Summit for top NYC operators hosted by 7shifts next month.
        2. Address exactly to: "Hi [Ops Director],"
        3. Write the ACTUAL copy Alex (the Sales Rep) will send. Do not use placeholders other than [Ops Director].
        4. Tone must be peer-to-peer, conversational B2B sales. Maximum 4 sentences.
        5. Leverage the data provided to make the invite highly specific, but DO NOT list metrics like a robot. Use the data as the justification for why they are being invited.
           - BAD: "Because you are a 4.5-star {cuisine} restaurant with {loc_count} locations, come to our event."
           - GOOD: "Managing the operational complexity of {loc_count} {cuisine} locations in the city isn't easy, which is why we're bringing top operators together to talk shop."

        SEGMENT DIRECTIVES FOR THE INVITE:
        - NET_NEW_TARGET: Frame the invite around their specific NYC footprint ({loc_count} locations). If POS is NOT 'Unknown', mention how other operators are leveraging {pos} integrations.
        - EXPANSION_OPPORTUNITY: Casually acknowledge they are already a customer at a few locations, but frame the event around scaling operations across their entire {global_locs} location footprint.
        - CREDIBLE_PARTNER: Frame the invite around their brand influence ({rating} rating, {cuisine} concept). Tell them we specifically want their perspective in the room.

        { 'CRITICAL: You MUST seamlessly integrate this rep note: ' + rep_notes if str(rep_notes).strip() != '' else 'End with a soft call to action asking if they are around for the dinner.' }

        Return a JSON object strictly matching this schema:
        {{
            "invitation_draft": "The conversational event invitation draft following the copywriting rules above.",
            "sales_context": "A sharp, 1-2 sentence internal battle card for the Sales Rep (Alex). Explain exactly WHY this company is a high-value target based on their segment and data."
        }}
        """

        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    response_mime_type="application/json"
                )
            )
            data = json.loads(response.text)
            drafts.append(data.get("invitation_draft", "ERROR: Missing Key"))
            contexts.append(data.get("sales_context", "ERROR: Missing Key"))
        except Exception as e:
            drafts.append(f"ERROR: {e}")
            contexts.append(f"ERROR: {e}")

        progress_bar.progress((i + 1) / len(selected_df))
        status_text.text(f"Processing {name} ({i+1}/{len(selected_df)})...")

    status_text.text("Generation complete.")
    return drafts, contexts

def export_to_excel(final_df):
    output = io.BytesIO()

    existing_mask = final_df['company_id'].notna() & (final_df['company_id'] != '')
    existing_df = final_df[existing_mask].copy()
    net_new_df = final_df[~existing_mask].copy()

    existing_export = pd.DataFrame({
        'company_id': existing_df['company_id'],
        'company_name': existing_df['crm_name'].fillna(existing_df['vendor_name']),
        'domain': existing_df['primary_domain'].fillna(existing_df['vendor_website']),
        'segment': existing_df['gtm_segment'],
        'sales_context': existing_df['sales_context'],
        'invitation_draft': existing_df['invitation_draft']
    })

    net_new_export = pd.DataFrame({
        'company_name': net_new_df['vendor_name'],
        'website': net_new_df['vendor_website'],
        'segment': net_new_df['gtm_segment'],
        'sales_context': net_new_df['sales_context'],
        'invitation_draft': net_new_df['invitation_draft']
    })

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        existing_export.to_excel(writer, sheet_name='Existing Customers', index=False)
        net_new_export.to_excel(writer, sheet_name='Net-New Prospects', index=False)

    return output.getvalue()

def main():
    st.title("🍽️ NYC Summit — Audience Selector")
    st.caption("Select, score, and activate your summit invite list. Generate personalised outreach drafts and export for Salesforce.")

    df = load_data()

    if 'gtm_segment' in df.columns:
        df['gtm_segment'] = df['gtm_segment'].astype(str).str.upper().str.strip()

    # --- PIPELINE OVERVIEW METRICS ---
    active_df = df[df['gtm_segment'].isin(['NET_NEW_TARGET', 'EXPANSION_OPPORTUNITY', 'CREDIBLE_PARTNER'])]
    seg_counts = active_df['gtm_segment'].value_counts()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Pipeline", len(active_df))
    m2.metric("🎯 Net New Targets", seg_counts.get('NET_NEW_TARGET', 0))
    m3.metric("📈 Expansion Opportunities", seg_counts.get('EXPANSION_OPPORTUNITY', 0))
    m4.metric("⭐ Credible Partners", seg_counts.get('CREDIBLE_PARTNER', 0))

    st.divider()

    # --- STATE MANAGEMENT ---
    if 'working_df' not in st.session_state:
        working_df = df.copy()

        if 'crm_name' in working_df.columns:
            working_df['Account_Name'] = working_df['crm_name'].replace('', pd.NA).fillna(working_df['vendor_name'])
        else:
            working_df['Account_Name'] = working_df['vendor_name']

        working_df['Account_Type'] = np.where(
            working_df['company_id'].notna() & (working_df['company_id'] != ''),
            'Current Customer',
            'Prospect'
        )

        working_df['Segment_Label'] = working_df['gtm_segment'].map(SEGMENT_LABELS).fillna(working_df['gtm_segment'])

        working_df['Select'] = False
        working_df['Approve'] = False
        working_df['invitation_draft'] = ""
        working_df['sales_context'] = ""
        working_df['rep_added_context'] = ""

        working_df['Select'] = working_df['Select'].astype(bool)
        working_df['Approve'] = working_df['Approve'].astype(bool)
        working_df['invitation_draft'] = working_df['invitation_draft'].astype(str)
        working_df['sales_context'] = working_df['sales_context'].astype(str)
        working_df['rep_added_context'] = working_df['rep_added_context'].astype(str)

        working_df['total_nyc_locations'] = pd.to_numeric(working_df['total_nyc_locations'], errors='coerce').fillna(0)
        working_df['global_location_count'] = pd.to_numeric(working_df['global_location_count'], errors='coerce').fillna(0)

        if 'avg_nyc_rating' in working_df.columns:
            working_df['avg_nyc_rating'] = pd.to_numeric(working_df['avg_nyc_rating'], errors='coerce')

        st.session_state.working_df = working_df

    # --- SIDEBAR: AUTO-SELECTION ENGINE ---
    st.sidebar.header("🎯 Auto-Selection Engine")
    st.sidebar.caption("Define your audience size and segment mix, then auto-select the top-ranked targets.")

    max_val = max(1, len(df))
    total_target = st.sidebar.number_input("Total Invitees", min_value=1, max_value=max_val, value=30)

    available_segments = sorted([
        seg for seg in df['gtm_segment'].dropna().unique().tolist()
        if seg not in ['PENDING_RESOLUTION', 'UNCLASSIFIED']
    ])

    st.sidebar.subheader("Segment Breakdown (%)")
    allocations = {}
    default_vals = {'CREDIBLE_PARTNER': 10, 'EXPANSION_OPPORTUNITY': 40, 'NET_NEW_TARGET': 50}

    for seg in available_segments:
        label = SEGMENT_LABELS.get(seg, seg)
        allocations[seg] = st.sidebar.number_input(label, min_value=0, max_value=100, value=default_vals.get(seg, 0), step=5)

    total_pct = sum(allocations.values())

    if total_pct != 100:
        st.sidebar.warning(f"⚠️ Allocation is **{total_pct}%** — must equal 100%.")

    if st.sidebar.button("Auto-Select Top Targets", type="primary", disabled=(total_pct != 100)):
        st.session_state.working_df['Select'] = False

        if 'expansion_potential_index' in st.session_state.working_df.columns:
            st.session_state.working_df['expansion_potential_index'] = pd.to_numeric(
                st.session_state.working_df['expansion_potential_index'], errors='coerce'
            ).fillna(0)
            sorted_df = st.session_state.working_df.sort_values(
                by=['expansion_potential_index', 'total_nyc_locations'],
                ascending=[False, False]
            )
        else:
            sorted_df = st.session_state.working_df.sort_values(
                by=['location_whitespace', 'total_nyc_locations'],
                ascending=[False, False]
            )

        for seg, pct in allocations.items():
            if pct > 0:
                count = int(round((pct / 100.0) * total_target))
                seg_indices = sorted_df[sorted_df['gtm_segment'] == seg].head(count).index
                st.session_state.working_df.loc[seg_indices, 'Select'] = True
        st.rerun()

    # --- STEP 1: AUDIENCE SHORTLISTING ---
    st.header("Step 1: Build Your Shortlist")

    col_filter1, col_filter2 = st.columns([1, 2])
    with col_filter1:
        account_filter = st.selectbox("Account Type", ["All", "Current Customer", "Prospect"])
    with col_filter2:
        segment_filter = st.multiselect(
            "Segment",
            options=available_segments,
            default=available_segments,
            format_func=lambda x: SEGMENT_LABELS.get(x, x)
        )

    active_segments = segment_filter if segment_filter else available_segments
    filtered_mask = st.session_state.working_df['gtm_segment'].isin(active_segments)
    if account_filter != "All":
        filtered_mask = filtered_mask & (st.session_state.working_df['Account_Type'] == account_filter)

    filtered_df = st.session_state.working_df[filtered_mask].sort_values(
        by=['Select', 'total_nyc_locations'],
        ascending=[False, False]
    )

    display_cols = ['Select', 'Account_Name', 'Account_Type', 'Segment_Label', 'expansion_potential_index', 'total_nyc_locations', 'avg_nyc_rating']
    display_cols = [col for col in display_cols if col in filtered_df.columns]

    edited_selection = st.data_editor(
        filtered_df[display_cols],
        hide_index=True,
        key="audience_editor",
        use_container_width=True,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", width="small"),
            "Account_Name": st.column_config.TextColumn("Account", width="large"),
            "Account_Type": st.column_config.TextColumn("Type", width="small"),
            "Segment_Label": st.column_config.TextColumn("Segment", width="medium"),
            "expansion_potential_index": st.column_config.NumberColumn(
                "Expansion Score",
                help="Predictive revenue proxy based on location whitespace and plan upgrade delta.",
                format="%.1fx"
            ),
            "total_nyc_locations": st.column_config.NumberColumn("NYC Locations", width="small"),
            "avg_nyc_rating": st.column_config.NumberColumn(
                "Avg Rating",
                help="Average Google Review Rating across NYC locations.",
                format="%.1f ⭐",
            ),
        },
        disabled=[col for col in display_cols if col != 'Select']
    )

    if st.session_state.get("audience_editor"):
        for row_idx, edit_values in st.session_state.audience_editor["edited_rows"].items():
            actual_df_idx = filtered_df.index[int(row_idx)]
            if "Select" in edit_values:
                st.session_state.working_df.at[actual_df_idx, 'Select'] = edit_values["Select"]

    selected_count = st.session_state.working_df['Select'].sum()
    st.caption(f"**{selected_count}** companies selected across all segments.")

    st.divider()

    # --- STEP 2: BASELINE GENERATION ---
    st.header("Step 2: Generate Drafts")
    st.caption("Generates a personalised invitation draft and internal sales context for each selected account.")

    if st.button("Generate Baseline for Selected", type="primary", disabled=(selected_count == 0)):
        target_mask = st.session_state.working_df['Select'] == True
        targets = st.session_state.working_df[target_mask]
        drafts, contexts = generate_messaging_and_context(targets)
        st.session_state.working_df.loc[target_mask, 'invitation_draft'] = drafts
        st.session_state.working_df.loc[target_mask, 'sales_context'] = contexts
        st.rerun()

    # --- STEP 3: REP REVIEW & HYPER-PERSONALIZATION ---
    review_mask = (st.session_state.working_df['Select'] == True) & (st.session_state.working_df['sales_context'] != "")
    review_df = st.session_state.working_df[review_mask]

    if not review_df.empty:
        st.divider()
        st.header("Step 3: Review & Personalise")
        st.caption("Add a note in **Rep Context** to trigger a hyper-personalised regeneration for that account.")

        review_cols = ['Approve', 'Account_Name', 'Segment_Label', 'sales_context', 'rep_added_context', 'invitation_draft']
        review_cols = [col for col in review_cols if col in review_df.columns]

        edited_review = st.data_editor(
            review_df[review_cols],
            hide_index=True,
            key="review_editor",
            use_container_width=True,
            column_config={
                "Approve": st.column_config.CheckboxColumn("✓ Approve", width="small"),
                "Account_Name": st.column_config.TextColumn("Account", width="medium"),
                "Segment_Label": st.column_config.TextColumn("Segment", width="small"),
                "sales_context": st.column_config.TextColumn("Why This Account", width="medium"),
                "rep_added_context": st.column_config.TextColumn("Rep Context", width="medium"),
                "invitation_draft": st.column_config.TextColumn("Invitation Draft", width="large"),
            },
            disabled=[col for col in review_cols if col not in ['Approve', 'rep_added_context', 'invitation_draft']]
        )

        if st.session_state.get("review_editor"):
            for row_idx, edit_values in st.session_state.review_editor["edited_rows"].items():
                actual_df_idx = review_df.index[int(row_idx)]
                if "Approve" in edit_values:
                    st.session_state.working_df.at[actual_df_idx, 'Approve'] = edit_values["Approve"]
                if "rep_added_context" in edit_values:
                    st.session_state.working_df.at[actual_df_idx, 'rep_added_context'] = edit_values["rep_added_context"]
                if "invitation_draft" in edit_values:
                    st.session_state.working_df.at[actual_df_idx, 'invitation_draft'] = edit_values["invitation_draft"]

        if st.button("🔄 Regenerate with Rep Notes"):
            regen_mask = (
                (st.session_state.working_df['Select'] == True) &
                (st.session_state.working_df['rep_added_context'].str.strip() != "")
            )
            regen_targets = st.session_state.working_df[regen_mask]
            if not regen_targets.empty:
                drafts, contexts = generate_messaging_and_context(regen_targets)
                st.session_state.working_df.loc[regen_mask, 'invitation_draft'] = drafts
                st.session_state.working_df.loc[regen_mask, 'sales_context'] = contexts
                st.rerun()

        approved_count = st.session_state.working_df.loc[review_df.index, 'Approve'].sum()

        if approved_count > 0:
            st.divider()
            st.success(f"**{approved_count}** records approved and ready for export.")
            excel_bytes = export_to_excel(
                st.session_state.working_df[st.session_state.working_df['Approve'] == True]
            )
            st.download_button(
                label="📥 Download Activation List",
                data=excel_bytes,
                file_name="7shifts_summit_activation_list.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

if __name__ == "__main__":
    main()
