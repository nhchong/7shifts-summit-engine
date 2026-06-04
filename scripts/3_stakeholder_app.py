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
st.set_page_config(page_title="7shifts | Audience Builder", layout="wide")
load_dotenv()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SEGMENT_LABELS = {
    'NET_NEW_TARGET':        '🎯 Net New Target',
    'EXPANSION_OPPORTUNITY': '📈 Expansion Opportunity',
    'CREDIBLE_PARTNER':      '⭐ Credible Partner',
    'EXPANSION_PARTNER':     '🏆 Expansion Partner',
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

def interpret_audience_prompt(prompt, available_segments, seg_counts):
    context = "\n".join([
        f"- {SEGMENT_LABELS.get(s, s)}: {seg_counts.get(s, 0)} records available"
        for s in available_segments
    ])
    instruction = f"""You configure an audience selection engine for a 7shifts NYC restaurant summit.

Segment definitions:
- EXPANSION_PARTNER: Fully deployed 7shifts customer with plan upgrade potential — highest value, both credible and growable.
- CREDIBLE_PARTNER: Fully deployed 7shifts customer with strong brand presence — adds room credibility.
- EXPANSION_OPPORTUNITY: Existing customer with location or plan growth whitespace — upsell pipeline.
- NET_NEW_TARGET: High-influence prospect not yet on 7shifts — acquisition pipeline.

Available segments and record counts:
{context}

User goal: "{prompt}"

Return JSON matching this schema exactly:
{{
  "total_target": <integer, default 30 unless user specifies>,
  "allocations": {{{", ".join(f'"{s}": <int 0-100>' for s in available_segments)}}},
  "weights": {{"footprint": <int 0-10>, "rating": <int 0-10>, "expansion": <int 0-10>}},
  "reasoning": "<one sentence explaining what this configuration optimises for>"
}}

Rules: allocation values must sum to exactly 100. Only allocate >0 to segments with records available.
"""
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=instruction,
        config=types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json")
    )
    data = json.loads(response.text)
    alloc = data.get('allocations', {})
    total = sum(alloc.values()) or 1
    if total != 100:
        alloc = {k: round(v * 100 / total) for k, v in alloc.items()}
        diff = 100 - sum(alloc.values())
        if diff and alloc:
            alloc[max(alloc, key=alloc.get)] += diff
        data['allocations'] = alloc
    return data


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
    st.title("Audience Selector")

    df = load_data()

    if 'gtm_segment' in df.columns:
        df['gtm_segment'] = df['gtm_segment'].astype(str).str.upper().str.strip()

    # --- PIPELINE OVERVIEW METRICS ---
    active_df = df[df['gtm_segment'].isin(['NET_NEW_TARGET', 'EXPANSION_OPPORTUNITY', 'CREDIBLE_PARTNER', 'EXPANSION_PARTNER'])]
    seg_counts = active_df['gtm_segment'].value_counts()

    seg_metric_labels = {
        'NET_NEW_TARGET':        '🎯 Net New Targets',
        'EXPANSION_OPPORTUNITY': '📈 Expansion Opportunities',
        'CREDIBLE_PARTNER':      '⭐ Credible Partners',
        'EXPANSION_PARTNER':     '🏆 Expansion Partners',
    }
    nonempty_segs = [s for s in seg_metric_labels if seg_counts.get(s, 0) > 0]
    metric_cols = st.columns(1 + len(nonempty_segs))
    metric_cols[0].metric("Total Pipeline", len(active_df))
    for col, seg in zip(metric_cols[1:], nonempty_segs):
        col.metric(seg_metric_labels[seg], seg_counts[seg])

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

    # --- SIDEBAR: AI AUDIENCE BUILDER ---
    _seg_order = ['EXPANSION_PARTNER', 'EXPANSION_OPPORTUNITY', 'CREDIBLE_PARTNER', 'NET_NEW_TARGET']
    _existing_segs = df['gtm_segment'].dropna().unique().tolist()
    available_segments = [s for s in _seg_order if s in _existing_segs]

    seg_counts_sidebar = df[df['gtm_segment'].isin(available_segments)]['gtm_segment'].value_counts().to_dict()

    n_prospects  = seg_counts_sidebar.get('NET_NEW_TARGET', 0)
    n_existing   = sum(seg_counts_sidebar.get(s, 0) for s in ['EXPANSION_OPPORTUNITY', 'EXPANSION_PARTNER', 'CREDIBLE_PARTNER'])

    st.sidebar.header("Build Your Audience")
    st.sidebar.caption(f"{n_prospects + n_existing} targets · {n_prospects} prospects · {n_existing} existing customers")

    audience_prompt = st.sidebar.text_area(
        label="audience_prompt",
        placeholder='e.g. "30 invites, mix of new prospects and existing customers"',
        height=100,
        key="audience_prompt_input",
        label_visibility="collapsed"
    )

    if st.sidebar.button("Build My List", type="primary", use_container_width=True, disabled=not (audience_prompt or "").strip()):
        with st.spinner("Building your audience..."):
            config = interpret_audience_prompt(audience_prompt, available_segments, seg_counts_sidebar)
            st.session_state.ai_config = config

            wdf = st.session_state.working_df
            wdf['expansion_potential_index'] = pd.to_numeric(wdf['expansion_potential_index'], errors='coerce').fillna(0)
            st.session_state.working_df['Select'] = False

            w = config.get('weights', {})
            w_f = w.get('footprint', 5)
            w_r = w.get('rating', 5)
            w_e = w.get('expansion', 5)
            total_w = (w_f + w_r + w_e) or 1
            epi_max = wdf['expansion_potential_index'].clip(lower=0).max() or 1
            priority = (
                (w_f / total_w) * wdf['market_footprint_percentile'].fillna(0) +
                (w_r / total_w) * wdf['market_rating_percentile'].fillna(0) +
                (w_e / total_w) * (wdf['expansion_potential_index'].clip(lower=0) / epi_max)
            )
            sorted_df = wdf.assign(priority_score=priority).sort_values('priority_score', ascending=False)

            total_target = config.get('total_target', 30)
            for seg, pct in config.get('allocations', {}).items():
                if pct > 0:
                    count = int(round((pct / 100.0) * total_target))
                    idx = sorted_df[sorted_df['gtm_segment'] == seg].head(count).index
                    st.session_state.working_df.loc[idx, 'Select'] = True
        st.rerun()

    if 'ai_config' in st.session_state:
        cfg = st.session_state.ai_config
        st.sidebar.success(cfg.get('reasoning', ''))
        total_target = cfg.get('total_target', 30)
        lines = [f"**{total_target} invites**"]
        for seg, pct in sorted(cfg.get('allocations', {}).items(), key=lambda x: -x[1]):
            if pct > 0:
                count = int(round(pct / 100 * total_target))
                lines.append(f"• {SEGMENT_LABELS.get(seg, seg)}: {count}")
        st.sidebar.caption("\n\n".join(lines))

    # --- STEP 1: AUDIENCE SHORTLISTING ---
    st.header("Your Shortlist")

    segment_filter = st.multiselect(
        "Filter by Segment",
        options=available_segments,
        default=available_segments,
        format_func=lambda x: SEGMENT_LABELS.get(x, x)
    )

    # Recompute priority_score using weights from last AI run, or equal defaults
    _w = st.session_state.get('ai_config', {}).get('weights', {})
    _w_f = _w.get('footprint', 5)
    _w_r = _w.get('rating', 5)
    _w_e = _w.get('expansion', 5)
    _wdf = st.session_state.working_df
    _total_w = (_w_f + _w_r + _w_e) or 1
    _epi_max = _wdf['expansion_potential_index'].clip(lower=0).max() or 1
    st.session_state.working_df['priority_score'] = (
        (_w_f / _total_w) * _wdf['market_footprint_percentile'].fillna(0) +
        (_w_r / _total_w) * _wdf['market_rating_percentile'].fillna(0) +
        (_w_e / _total_w) * (_wdf['expansion_potential_index'].clip(lower=0) / _epi_max)
    )

    active_segments = segment_filter if segment_filter else available_segments
    filtered_mask = st.session_state.working_df['gtm_segment'].isin(active_segments)

    filtered_df = st.session_state.working_df[filtered_mask].sort_values(
        by=['Select', 'priority_score'],
        ascending=[False, False]
    )

    display_cols = ['Select', 'Account_Name', 'Segment_Label', 'priority_score', 'expansion_potential_index', 'total_nyc_locations', 'avg_nyc_rating']
    display_cols = [col for col in display_cols if col in filtered_df.columns]

    edited_selection = st.data_editor(
        filtered_df[display_cols],
        hide_index=True,
        key="audience_editor",
        use_container_width=True,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", width="small"),
            "Account_Name": st.column_config.TextColumn("Account", width="large"),
            "Segment_Label": st.column_config.TextColumn("Segment", width="medium"),
            "priority_score": st.column_config.ProgressColumn(
                "Priority Score",
                help="Weighted composite of footprint, brand quality, and expansion potential. Adjust signal weights in the sidebar.",
                min_value=0,
                max_value=1,
                format="%.2f",
            ),
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
    st.header("Outreach Drafts")

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
        st.header("Review & Personalise")

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
