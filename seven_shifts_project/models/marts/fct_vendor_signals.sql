{{ config(materialized='table') }}

WITH crm AS (
    SELECT 
        company_id,
        company_name,
        LOWER(REPLACE(REPLACE(REPLACE(domain, 'https://', ''), 'http://', ''), 'www.', '')) AS clean_domain,
        plan_name AS crm_plan,
        locations_in_7shifts AS crm_location_count
    FROM {{ ref('stg_crm_accounts') }}
),

target_companies AS (
    SELECT * FROM {{ ref('stg_companies') }}
    WHERE num_locations <= 50 OR num_locations IS NULL
),

target_locations AS (
    SELECT * FROM {{ ref('stg_locations') }}
    WHERE business_type IN (
        '["Restaurant"]',
        '["Coffee Shop"]',
        '["Dessert & Bakery"]',
        '["Juice Bar"]',
        '["Bar"]'
    )
    AND operational_status = 'OPERATIONAL'
),

vendor_signals_agg AS (
    SELECT 
        c.account_id AS vendor_id,
        c.company_name AS vendor_name,
        c.website AS vendor_website,
        LOWER(REPLACE(REPLACE(REPLACE(c.website, 'https://', ''), 'http://', ''), 'www.', '')) AS clean_domain,
        COUNT(location_id) AS global_location_count,
        c.pos_vendor AS vendor_pos,
        COUNT(DISTINCT CASE WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island', 'Flushing', 'Astoria', 'Long Island City', 'Jackson Heights') THEN l.google_place_id ELSE NULL END) AS total_nyc_locations,
        ROUND(
            SUM(l.review_rating * l.num_reviews) / NULLIF(SUM(l.num_reviews), 0), 
            1
        ) AS avg_nyc_rating,
        GROUP_CONCAT(DISTINCT LOWER(l.cuisine_type)) AS cuisines,
        MAX(l.price_tier) AS price_tier,
        MAX(l.instagram_url) AS instagram_url
    FROM target_companies c
    LEFT JOIN target_locations l ON c.account_id = l.account_id
    GROUP BY c.account_id, c.company_name, c.website, c.pos_vendor
),

master_join AS (
    SELECT 
        COALESCE(c.clean_domain, v.clean_domain) AS primary_domain,
        c.company_id,
        c.company_name AS crm_name,
        c.crm_plan,
        c.crm_location_count,
        v.vendor_id,
        v.vendor_name,
        v.vendor_website,
        v.global_location_count,
        v.total_nyc_locations,
        v.avg_nyc_rating,
        v.vendor_pos,
        v.cuisines,
        v.price_tier,
        v.instagram_url
    FROM crm c
    FULL OUTER JOIN vendor_signals_agg v 
        ON c.clean_domain = v.clean_domain
),

base_metrics AS (
    SELECT 
        *,
        (COALESCE(global_location_count, 0) - COALESCE(crm_location_count, 0)) AS location_whitespace,
        
        -- Derive ACV Proxy from capital indicator
        CASE 
            WHEN price_tier = '$$$$' THEN 4.0
            WHEN price_tier = '$$$' THEN 3.0
            WHEN price_tier = '$$' THEN 2.0
            ELSE 1.0 
        END AS acv_proxy,
        
        -- Derive Upgrade Delta based on current licensing limitations
        CASE 
            WHEN crm_plan IN ('Comp', 'Free') THEN 3.0
            WHEN crm_plan = 'Essentials' THEN 2.0
            ELSE 0.0 
        END AS plan_upgrade_delta,

        PERCENT_RANK() OVER (ORDER BY COALESCE(total_nyc_locations, 0) ASC) AS market_footprint_percentile,
        PERCENT_RANK() OVER (ORDER BY COALESCE(avg_nyc_rating, 0) ASC) AS market_rating_percentile
    FROM master_join
),

-- Calculate the final Expansion Potential Index (EPI)
expansion_scoring AS (
    SELECT 
        *,
        -- (Whitespace * ACV Proxy) + (Deployed Locations * Upgrade Delta)
        ((location_whitespace * acv_proxy) + (COALESCE(crm_location_count, 0) * plan_upgrade_delta)) AS expansion_potential_index
    FROM base_metrics
),

final_segments AS (
    SELECT 
        *,
        ((market_footprint_percentile + market_rating_percentile) / 2.0) AS composite_influence_score,
        
        CASE 
            WHEN company_id IS NOT NULL AND vendor_id IS NULL THEN 'PENDING_RESOLUTION'
            
            -- Route dynamically based on the calculated index being mathematically viable
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL 
                 AND expansion_potential_index > 0
            THEN 'EXPANSION_OPPORTUNITY'
            
            WHEN company_id IS NULL AND vendor_id IS NOT NULL 
                 AND ((market_footprint_percentile + market_rating_percentile) / 2.0) >= 0.5 
            THEN 'NET_NEW_TARGET'
            
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL 
                 AND location_whitespace <= 0
                 AND ((market_footprint_percentile + market_rating_percentile) / 2.0) >= 0.8
            THEN 'CREDIBLE_PARTNER'
            
            ELSE 'UNCLASSIFIED'
        END AS gtm_segment
    FROM expansion_scoring
)

SELECT * FROM final_segments