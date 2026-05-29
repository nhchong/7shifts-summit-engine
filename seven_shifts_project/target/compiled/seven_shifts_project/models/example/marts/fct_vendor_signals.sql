-- models/marts/fct_vendor_signals.sql

WITH base_companies AS (
    SELECT * FROM main."stg_companies"
),

base_locations AS (
    SELECT * FROM main."stg_locations"
),

aggregated AS (
    SELECT 
        c.account_id AS vendor_id,
        c.company_name AS vendor_name,
        c.website AS vendor_website,
        c.num_locations AS global_location_count,
        c.pos_vendor AS vendor_pos,
        
        SUM(
            CASE 
                WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island') THEN 1 
                ELSE 0 
            END
        ) AS total_nyc_locations,
        
        ROUND(
            AVG(
                CASE 
                    WHEN l.city IN ('New York', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island') THEN l.review_rating 
                    ELSE NULL 
                END
            ), 1
        ) AS avg_nyc_rating,
        
        GROUP_CONCAT(DISTINCT l.cuisine_type) AS cuisines

    FROM base_companies c
    LEFT JOIN base_locations l ON c.account_id = l.account_id
    GROUP BY 1, 2, 3, 4, 5
)

SELECT * FROM aggregated