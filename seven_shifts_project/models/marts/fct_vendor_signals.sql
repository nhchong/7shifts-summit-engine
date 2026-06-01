{{ config(materialized='table') }}

WITH crm AS (
    SELECT 
        company_id,
        company_name,
        LOWER(REPLACE(REPLACE(domain, 'www.', ''), 'https://', '')) AS clean_domain,
        plan_name AS crm_plan,
        locations_in_7shifts AS crm_location_count
    FROM {{ ref('stg_crm_accounts') }}
),

-- 1. Apply business logic to filter out massive enterprise chains
target_companies AS (
    SELECT * FROM {{ ref('stg_companies') }}
    WHERE num_locations <= 50 OR num_locations IS NULL
),

-- 2. Apply business logic to filter out non-restaurant retail and remove dead locations
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

-- 3. Aggregate using the newly filtered CTEs
vendor_signals_agg AS (
    SELECT 
        c.account_id AS vendor_id,
        c.company_name AS vendor_name,
        c.website AS vendor_website,
        LOWER(REPLACE(REPLACE(c.website, 'www.', ''), 'https://', '')) AS clean_domain,
        COUNT(location_id) AS global_location_count,
        c.pos_vendor AS vendor_pos,
        COUNT(DISTINCT CASE WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island', 'Flushing', 'Astoria', 'Long Island City', 'Jackson Heights') THEN l.google_place_id ELSE NULL END) AS total_nyc_locations,
        ROUND(
            SUM(l.review_rating * l.num_reviews) / NULLIF(SUM(l.num_reviews), 0), 
            1
        ) AS avg_nyc_rating,
        GROUP_CONCAT(DISTINCT LOWER(l.cuisine_type)) AS cuisines
    FROM target_companies c
    LEFT JOIN target_locations l ON c.account_id = l.account_id
    GROUP BY c.account_id, c.company_name, c.website, c.pos_vendor
),

-- 4. Initial deterministic join on domain names
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
        v.cuisines
    FROM crm c
    FULL OUTER JOIN vendor_signals_agg v 
        ON c.clean_domain = v.clean_domain
),

-- 5. Calculate base metrics and dynamic market percentiles
base_metrics AS (
    SELECT 
        *,
        (COALESCE(global_location_count, 0) - COALESCE(crm_location_count, 0)) AS location_whitespace,
        
        -- Window functions calculating relative percentile rank (0.0 to 1.0)
        PERCENT_RANK() OVER (ORDER BY COALESCE(total_nyc_locations, 0) ASC) AS market_footprint_percentile,
        PERCENT_RANK() OVER (ORDER BY COALESCE(avg_nyc_rating, 0) ASC) AS market_rating_percentile
    FROM master_join
),

-- 6. Apply dynamic segmentation matrix
final_segments AS (
    SELECT 
        *,
        ((market_footprint_percentile + market_rating_percentile) / 2.0) AS composite_influence_score,
        
        CASE 
            -- Priority 0: Protect the system from unmatched data ghosts
            WHEN company_id IS NOT NULL AND vendor_id IS NULL THEN 'PENDING_RESOLUTION'
            
            -- Priority 1: Cross-sell revenue overrides everything else.
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL 
                 AND (location_whitespace >= 1 OR crm_plan IN ('Essentials', 'Comp'))
            THEN 'EXPANSION_OPPORTUNITY'
            
            -- Priority 2: Strategic Land (Net-New prospect in the top 50% of the market)
            WHEN company_id IS NULL AND vendor_id IS NOT NULL 
                 AND ((market_footprint_percentile + market_rating_percentile) / 2.0) >= 0.5 
            THEN 'NET_NEW_TARGET'
            
            -- Priority 3: The Advocate / VIP (Existing customer, fully deployed, top 20% influence)
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL 
                 AND location_whitespace <= 0
                 AND ((market_footprint_percentile + market_rating_percentile) / 2.0) >= 0.8
            THEN 'CREDIBLE_PARTNER'
            
            -- Fallback: Bottom 50% of prospects and un-influential SMB customers
            ELSE 'UNCLASSIFIED'
        END AS gtm_segment
    FROM base_metrics
)

SELECT * FROM final_segments