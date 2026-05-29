
    
    create view main."stg_locations" as
    SELECT * FROM main."locations"
WHERE LOWER(cuisine_type) NOT LIKE '%pharmacy%'
  AND LOWER(cuisine_type) NOT LIKE '%cvs%'
  AND LOWER(cuisine_type) NOT LIKE '%apparel%'
  AND LOWER(cuisine_type) NOT LIKE '%convenience%'
  AND LOWER(cuisine_type) NOT LIKE '%grocery%'
  AND LOWER(cuisine_type) NOT LIKE '%supermarket%'
  AND LOWER(cuisine_type) NOT LIKE '%department store%';