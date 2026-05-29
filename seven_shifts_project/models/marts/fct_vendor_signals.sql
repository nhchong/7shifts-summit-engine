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

vendor_signals_agg AS (
    SELECT 
        c.account_id AS vendor_id,
        c.company_name AS vendor_name,
        c.website AS vendor_website,
        LOWER(REPLACE(REPLACE(c.website, 'www.', ''), 'https://', '')) AS clean_domain,
        c.num_locations AS global_location_count,
        c.pos_vendor AS vendor_pos,
        SUM(CASE WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island') THEN 1 ELSE 0 END) AS total_nyc_locations,
        ROUND(AVG(CASE WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island') THEN l.review_rating ELSE NULL END), 1) AS avg_nyc_rating,
        GROUP_CONCAT(DISTINCT LOWER(l.cuisine_type)) AS cuisines
    FROM {{ ref('stg_companies') }} c
    LEFT JOIN {{ ref('stg_locations') }} l ON c.account_id = l.account_id
    GROUP BY c.account_id, c.company_name, c.website, c.num_locations, c.pos_vendor
),

master_join AS (
    -- Simulating FULL OUTER JOIN
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
    LEFT JOIN vendor_signals_agg v ON c.clean_domain = v.clean_domain
    
    UNION
    
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
    FROM vendor_signals_agg v
    LEFT JOIN crm c ON v.clean_domain = c.clean_domain
),

segmentation_layer AS (
    SELECT 
        primary_domain, 
        company_id, 
        crm_name, 
        crm_plan, 
        crm_location_count, 
        vendor_id, 
        vendor_name, 
        vendor_website, 
        global_location_count, 
        total_nyc_locations, 
        avg_nyc_rating, 
        vendor_pos, 
        cuisines, 
        (COALESCE(global_location_count, 0) - COALESCE(crm_location_count, 0)) AS location_whitespace,
        CASE 
            -- Tier 1 & 2: Successfully Joined Records
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL AND total_nyc_locations >= 3 AND avg_nyc_rating >= 4.0 THEN 'CREDIBLE_PARTNER'
            WHEN company_id IS NOT NULL AND vendor_id IS NOT NULL AND ((COALESCE(global_location_count, 0) - COALESCE(crm_location_count, 0)) >= 2 OR crm_plan IN ('Essentials', 'Comp')) THEN 'EXPANSION_OPPORTUNITY'
            -- Pipeline Pass-Through: Orphaned CRM Records sent to Gemini
            WHEN company_id IS NOT NULL AND vendor_id IS NULL THEN 'PENDING_RESOLUTION'
            -- Pipeline Pass-Through: Orphaned Vendor Records
            WHEN company_id IS NULL AND vendor_id IS NOT NULL AND total_nyc_locations >= 1 THEN 'NET_NEW_TARGET'
            -- Catch-All
            ELSE 'UNCLASSIFIED'
        END AS gtm_segment
    FROM master_join
)

SELECT 
    primary_domain, 
    company_id, 
    crm_name, 
    crm_plan, 
    crm_location_count, 
    vendor_id, 
    vendor_name, 
    vendor_website, 
    global_location_count, 
    total_nyc_locations, 
    avg_nyc_rating, 
    vendor_pos, 
    cuisines, 
    location_whitespace,
    gtm_segment
FROM segmentation_layer
WHERE gtm_segment != 'UNCLASSIFIED'