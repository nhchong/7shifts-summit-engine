"""
Phase 3: Stakeholder Selection & Export UI
------------------------------------------
Streamlit application for interactive audience filtering, 
message drafting, context generation, and compliant Excel export.
Includes a Two-Pass Hyper-Personalization Loop for Sales Reps.
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

# Page Config
st.set_page_config(page_title="7shifts Summit Selector", layout="wide")
load_dotenv()

@st.cache_data
def load_data():
    conn = sqlite3.connect('data/7shifts_nyc_data.db')
    df = pd.read_sql_query("SELECT * FROM fct_activation_ready", conn)
    conn.close()
    
    df['is_franchise_str'] = df['is_franchise'].astype(str).str.strip().str.upper()
    return df[df['is_franchise_str'].isin(['0', '0.0', 'FALSE', 'UNKNOWN'])]

def generate_messaging_and_context(selected_df):
    """Generates structured JSON containing both drafts and sales rationale, factoring in rep notes."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
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
        # Change the fallback from the awkward string to 'Unknown'
        pos = row['vendor_pos'] if pd.notna(row['vendor_pos']) and str(row['vendor_pos']).strip() != '' else 'Unknown'
        
        rep_notes = row.get('rep_added_context', '')
        rep_notes_instruction = f"\nCRITICAL REP NOTES TO INTEGRATE: {rep_notes}" if str(rep_notes).strip() != '' else ""
        
        # Update the prompt to conditionally handle the 'Unknown' POS state
        prompt = f"""
        You are a GTM strategist generating targeted sales outreach data for 7shifts, a restaurant team management platform.
        Target Company: {name}
        GTM Segment: {segment}
        NYC Locations: {loc_count}
        Global Locations: {global_locs}
        POS System: {pos}
        Cuisine: {cuisine}
        Average Rating: {rating}{rep_notes_instruction}
        
        SEGMENT DEFINITIONS & EXPECTED CONTEXT:
        - NET_NEW_TARGET: Zero existing relationship. The sales context must highlight their total location count ({loc_count} NYC / {global_locs} Global). If the POS System is NOT 'Unknown', highlight their {pos} POS integration potential.
        - EXPANSION_OPPORTUNITY: Current customer using us at a fraction of their total locations. The sales context must explicitly call out the whitespace (Total locations vs. currently deployed) as the primary revenue driver for the rep.
        - CREDIBLE_PARTNER: Highly rated or influential brand. The sales context must focus on their brand influence ({rating} rating, {cuisine} concept) and why securing their logo elevates our market presence, even if the immediate location count is lower.
        
        Return a JSON object strictly matching this schema:
        {{
            "invitation_draft": "A concise, 2-sentence professional invitation to an exclusive VIP dinner for restaurant executives in NYC hosted by 7shifts. Address exactly to [Ops Director]. Personalize the message by explicitly acknowledging their scale ({loc_count} NYC locations) and their {cuisine} concept. If the POS System is NOT 'Unknown', reference integrating with their {pos} system.{ ' CRITICAL: You MUST also seamlessly integrate this rep note into the invitation: ' + rep_notes if str(rep_notes).strip() != '' else '' } No subject lines or generic sign-offs.",
            "sales_context": "A sharp, 1-2 sentence internal battle card for the Sales Rep (Alex). Explain exactly WHY this company is a high-value target using the segment definitions above. Cite their specific location footprint and segment. If the POS System is NOT 'Unknown', explicitly cite their POS data. If it is 'Unknown', do not mention POS systems at all."
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
    """Compiles the dataframe into Jamie's exact dual-sheet specification."""
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
    st.title("🍽️ Summit Audience Selector")
    df = load_data()
    
    if 'gtm_segment' in df.columns:
        df['gtm_segment'] = df['gtm_segment'].astype(str).str.upper().str.strip()
    
    if 'working_df' not in st.session_state:
        working_df = df.copy()

        # Coalesce CRM and Vendor names into a single clean UI column
        if 'crm_name' in working_df.columns:
            working_df['Account_Name'] = working_df['crm_name'].replace('', pd.NA).fillna(working_df['vendor_name'])
        else:
            working_df['Account_Name'] = working_df['vendor_name']

        # --- ADDED: Vectorized UX Mapping ---
        # Instantly categorizes records without iterating
        working_df['Account_Type'] = np.where(
            working_df['company_id'].notna() & (working_df['company_id'] != ''), 
            'Current Customer', 
            'Prospect'
        )

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
        
        st.session_state.working_df = working_df

    # --- SIDEBAR: AUTO-SELECTION ENGINE ---
    st.sidebar.header("🎯 Auto-Selection Engine")
    st.sidebar.markdown("Define your total audience size and segment breakdown.")
    
    max_val = max(1, len(df))
    total_target = st.sidebar.number_input("Total Invitees", min_value=1, max_value=max_val, value=30)
    
    # --- ADDED: Garbage Collection Filter ---
    # Removes PENDING_RESOLUTION and UNCLASSIFIED from the UI selections completely
    available_segments = sorted([seg for seg in df['gtm_segment'].dropna().unique().tolist() if seg not in ['PENDING_RESOLUTION', 'UNCLASSIFIED']])
    
    st.sidebar.subheader("Segment Breakdown (%)")
    allocations = {}
    
    default_vals = {'CREDIBLE_PARTNER': 10, 'EXPANSION_OPPORTUNITY': 40, 'NET_NEW_TARGET': 50}
    
    for seg in available_segments:
        def_val = default_vals.get(seg, 0)
        allocations[seg] = st.sidebar.number_input(f"{seg}", min_value=0, max_value=100, value=def_val, step=5)
        
    total_pct = sum(allocations.values())
    
    if total_pct != 100:
        st.sidebar.warning(f"⚠️ Current allocation: **{total_pct}%**. Must equal 100%.")
    
    if st.sidebar.button("🤖 Auto-Select Top Targets", type="primary", disabled=(total_pct != 100)):
        st.session_state.working_df['Select'] = False
        sorted_df = st.session_state.working_df.sort_values(
            by=['total_nyc_locations', 'global_location_count'], 
            ascending=[False, False]
        )
        
        for seg, pct in allocations.items():
            if pct > 0:
                count = int(round((pct / 100.0) * total_target))
                seg_mask = sorted_df['gtm_segment'] == seg
                seg_indices = sorted_df[seg_mask].head(count).index
                st.session_state.working_df.loc[seg_indices, 'Select'] = True
        st.rerun()

    # --- STEP 1: AUDIENCE SHORTLISTING ---
    st.header("Step 1: Build Shortlist")
    
    # --- ADDED: UX Filter for Account Type ---
    col_filter1, col_filter2 = st.columns([1, 3])
    with col_filter1:
        account_filter = st.selectbox("View by Account Type:", ["All", "Current Customer", "Prospect"])
    
    active_segments = [seg for seg, pct in allocations.items() if pct > 0]
    if not active_segments:
        active_segments = available_segments
        
    filtered_mask = st.session_state.working_df['gtm_segment'].isin(active_segments)
    
    # Apply the UX Account Type filter on top of the segment mask
    if account_filter != "All":
        filtered_mask = filtered_mask & (st.session_state.working_df['Account_Type'] == account_filter)
        
    filtered_df = st.session_state.working_df[filtered_mask].sort_values(
        by=['Select', 'total_nyc_locations'], 
        ascending=[False, False]
    )
    
    # --- ADDED: Account_Type into the Display Columns ---
    display_cols = ['Select', 'Account_Name', 'Account_Type', 'gtm_segment', 'total_nyc_locations', 'cuisines']
    display_cols = [col for col in display_cols if col in filtered_df.columns]
    
    edited_selection = st.data_editor(
        filtered_df[display_cols],
        hide_index=True,
        key="audience_editor",
        width="stretch",
        disabled=[col for col in display_cols if col != 'Select']
    )
    
    if st.session_state.get("audience_editor"):
        for row_idx, edit_values in st.session_state.audience_editor["edited_rows"].items():
            actual_df_idx = filtered_df.index[int(row_idx)]
            if "Select" in edit_values:
                st.session_state.working_df.at[actual_df_idx, 'Select'] = edit_values["Select"]

    selected_count = st.session_state.working_df['Select'].sum()
    st.caption(f"Total globally selected pipeline: **{selected_count}** companies.")

    # --- STEP 2: BASELINE GENERATION ---
    st.header("Step 2: Generate Baseline Context & Messaging")
    
    if st.button("Generate Baseline for Selected", type="primary") and selected_count > 0:
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
        st.header("Step 3: Review & Hyper-Personalize")
        st.markdown("Review context and drafts. To hyper-personalize an account, type a raw note in **Rep Added Context** and hit the regenerate button below.")
        
        review_cols = ['Approve', 'vendor_name', 'sales_context', 'rep_added_context', 'invitation_draft']
        review_cols = [col for col in review_cols if col in review_df.columns]
        
        edited_review = st.data_editor(
            review_df[review_cols],
            hide_index=True,
            key="review_editor",
            width="stretch",
            column_config={
                "sales_context": st.column_config.TextColumn("Sales Context (Why?)", width="medium"),
                "rep_added_context": st.column_config.TextColumn("Rep Added Context", width="medium"),
                "invitation_draft": st.column_config.TextColumn("Invitation Draft", width="large")
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

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🔄 Regenerate Edited Drafts"):
                regen_mask = (st.session_state.working_df['Select'] == True) & (st.session_state.working_df['rep_added_context'].str.strip() != "")
                regen_targets = st.session_state.working_df[regen_mask]
                
                if not regen_targets.empty:
                    drafts, contexts = generate_messaging_and_context(regen_targets)
                    st.session_state.working_df.loc[regen_mask, 'invitation_draft'] = drafts
                    st.session_state.working_df.loc[regen_mask, 'sales_context'] = contexts
                    st.rerun()

        approved_count = st.session_state.working_df.loc[review_df.index, 'Approve'].sum()
        
        # --- STEP 4: EXPORT ---
        if approved_count > 0:
            st.success(f"{approved_count} records approved and ready for target activation export.")
            
            final_export_df = st.session_state.working_df[st.session_state.working_df['Approve'] == True]
            excel_bytes = export_to_excel(final_export_df)
            
            st.download_button(
                label="📥 Download Activation List (Jamie's Format)",
                data=excel_bytes,
                file_name="7shifts_summit_activation_list.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

if __name__ == "__main__":
    main()