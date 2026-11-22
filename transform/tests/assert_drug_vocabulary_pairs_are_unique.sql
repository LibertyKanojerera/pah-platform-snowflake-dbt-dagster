-- The vocabulary's grain is the (gnrc_name, brnd_name) pair. A duplicate would
-- fan out every join on it and double-count claims.
select
    gnrc_name,
    brnd_name,
    count(*) as copies
from {{ ref('drug_vocabulary') }}
group by gnrc_name, brnd_name
having count(*) > 1
