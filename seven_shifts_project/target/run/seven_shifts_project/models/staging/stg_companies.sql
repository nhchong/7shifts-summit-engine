
    
    create view main."stg_companies" as
    SELECT * FROM main."companies"
WHERE num_locations <= 50 OR num_locations IS NULL;