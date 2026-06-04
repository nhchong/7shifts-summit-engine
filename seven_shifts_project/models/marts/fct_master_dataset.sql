{{ config(materialized='table') }}

WITH vendor_signals AS (
    SELECT * FROM {{ ref('fct_vendor_signals') }}
),

resolution_map AS (
    SELECT * FROM {{ source('main', 'entity_resolution_map') }}
),

-- Remove vendor clones identified by the fuzzy deduplication pass in Phase 1
deduped AS (
    SELECT vs.*
    FROM vendor_signals vs
    LEFT JOIN {{ source('main', 'duplicate_vendor_ids') }} d ON vs.vendor_id = d.vendor_id
    WHERE d.vendor_id IS NULL
),

-- Attach resolution results to PENDING_RESOLUTION rows, hydrating vendor attributes
-- from the matched vendor row in fct_vendor_signals
resolved AS (
    SELECT
        d.primary_domain,
        d.company_id,
        d.crm_name,
        d.crm_plan,
        d.crm_location_count,
        COALESCE(d.vendor_id, r.matched_vendor_id)                             AS vendor_id,
        COALESCE(d.vendor_name, v.vendor_name)                                 AS vendor_name,
        COALESCE(d.vendor_website, v.vendor_website)                           AS vendor_website,
        COALESCE(d.global_location_count, v.global_location_count)             AS global_location_count,
        COALESCE(d.total_nyc_locations, v.total_nyc_locations)                 AS total_nyc_locations,
        COALESCE(d.avg_nyc_rating, v.avg_nyc_rating)                           AS avg_nyc_rating,
        COALESCE(d.vendor_pos, v.vendor_pos)                                   AS vendor_pos,
        COALESCE(d.cuisines, v.cuisines)                                       AS cuisines,
        COALESCE(d.price_tier, v.price_tier)                                   AS price_tier,
        COALESCE(d.market_footprint_percentile, v.market_footprint_percentile) AS market_footprint_percentile,
        COALESCE(d.market_rating_percentile, v.market_rating_percentile)       AS market_rating_percentile
    FROM deduped d
    LEFT JOIN resolution_map r ON d.company_id = r.company_id
    LEFT JOIN vendor_signals v  ON r.matched_vendor_id = v.vendor_id
    -- Drop orphaned vendor rows that were claimed by a resolved CRM record
    WHERE NOT (
        d.company_id IS NULL
        AND d.vendor_id IN (SELECT matched_vendor_id FROM resolution_map)
    )
),

base_metrics AS (
    SELECT
        *,
        (COALESCE(global_location_count, 0) - COALESCE(crm_location_count, 0)) AS location_whitespace,
        CASE
            WHEN price_tier = '$$$$' THEN 4.0
            WHEN price_tier = '$$$'  THEN 3.0
            WHEN price_tier = '$$'   THEN 2.0
            ELSE 1.0
        END AS acv_proxy,
        CASE
            WHEN crm_plan IN ('Comp', 'Free') THEN 3.0
            WHEN crm_plan = 'Essentials'      THEN 2.0
            ELSE 0.0
        END AS plan_upgrade_delta
    FROM resolved
),

expansion_scoring AS (
    SELECT
        *,
        (location_whitespace * acv_proxy) + (COALESCE(crm_location_count, 0) * plan_upgrade_delta) AS expansion_potential_index
    FROM base_metrics
),

final_segments AS (
    SELECT
        *,
        CASE
            -- Quality gate: no NYC presence means no relevance to this market, regardless of event type
            WHEN total_nyc_locations = 0
                THEN 'UNCLASSIFIED'
            WHEN company_id IS NOT NULL AND vendor_id IS NULL
                THEN 'PENDING_RESOLUTION'
            -- Top tier: fully deployed + plan upgrade potential
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL
                 AND location_whitespace <= 0
                 AND expansion_potential_index > 0
                THEN 'EXPANSION_PARTNER'
            -- Fully deployed existing customer, no expansion lever
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL
                 AND location_whitespace <= 0
                THEN 'CREDIBLE_PARTNER'
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL
                 AND expansion_potential_index > 0
                THEN 'EXPANSION_OPPORTUNITY'
            -- All remaining prospects with NYC presence — score-based ranking happens at the UI layer
            WHEN company_id IS NULL AND vendor_id IS NOT NULL
                THEN 'NET_NEW_TARGET'
            ELSE 'UNCLASSIFIED'
        END AS gtm_segment
    FROM expansion_scoring
)

SELECT * FROM final_segments
