-- One row per drug: each (generic, brand) pair CMS reports, the grain at
-- which drug_vocabulary assigns molecule, route and product type (ADR-022).
-- Twelve rows where the Databricks build's drug dimension had eight: the
-- stored pair is the unit that carries meaning. Veletri appears under two
-- generic strings, and Tyvaso Refill Kit (an accessory) shares its generic
-- string with Tyvaso (a drug), so neither string alone is a key.
--
-- first_data_year and last_data_year say when the pair appears in Part D.
-- Claim counts stay in the facts.

with drugs as (
    select * from {{ ref('int_drugs__route_resolved') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['generic_name', 'brand_name']) }} as drug_key,
    generic_name,
    brand_name,
    molecule,
    route,
    product_type,
    first_data_year,
    last_data_year
from drugs
