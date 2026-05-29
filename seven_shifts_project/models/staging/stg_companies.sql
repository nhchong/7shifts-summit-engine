SELECT * FROM {{ source('main', 'companies') }}
WHERE num_locations <= 50 OR num_locations IS NULL