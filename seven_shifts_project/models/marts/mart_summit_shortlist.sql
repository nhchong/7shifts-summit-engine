-- models/marts/mart_summit_shortlist.sql

WITH stratified_ranking AS (
    SELECT 
        company_id,
        COALESCE(crm_name, vendor_name) AS company_name,
        COALESCE(primary_domain, vendor_website) AS domain_or_website,
        gtm_segment AS segment,
        avg_nyc_rating,
        total_nyc_locations,
        -- Rank within each segment to ensure an even distribution
        ROW_NUMBER() OVER (
            PARTITION BY gtm_segment 
            ORDER BY avg_nyc_rating DESC, location_whitespace DESC
        ) as rank_in_segment
    FROM {{ ref('fct_activation_ready') }}
    WHERE gtm_segment IN ('CREDIBLE_PARTNER', 'EXPANSION_OPPORTUNITY', 'NET_NEW_TARGET')
),

shortlist AS (
    SELECT *
    FROM stratified_ranking
    -- Take top 10 from each segment for a total of 30
    WHERE rank_in_segment <= 10
)

SELECT 
    company_id,
    company_name,
    domain_or_website,
    segment,
    avg_nyc_rating,
    total_nyc_locations
FROM shortlist
ORDER BY segment, avg_nyc_rating DESC