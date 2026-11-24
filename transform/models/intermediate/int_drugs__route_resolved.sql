-- One row per drug: each (generic, brand) pair CMS reports, with the
-- molecule, route and product type drug_vocabulary assigns it, and how
-- often and over which years the pair appears in claims.
--
-- Resolution is an exact join on both stored strings (ADR-022). The join
-- is a left join on purpose: a pair the vocabulary lacks becomes a null
-- route that fails not_null, never a claim dropped in silence. That is
-- ADR-002's lesson, from the Databricks build, kept as a property.

with claims as (
    select * from {{ ref('stg_cms__part_d_claims') }}
),

pairs as (
    select
        generic_name,
        brand_name,
        count(*) as claim_rows,
        sum(total_claims) as total_claims,
        min(data_year) as first_data_year,
        max(data_year) as last_data_year
    from claims
    group by generic_name, brand_name
),

vocabulary as (
    select * from {{ ref('drug_vocabulary') }}
)

select
    pairs.generic_name,
    pairs.brand_name,
    vocabulary.molecule,
    vocabulary.route,
    vocabulary.product_type,
    pairs.claim_rows,
    pairs.total_claims,
    pairs.first_data_year,
    pairs.last_data_year
from pairs
left join vocabulary
    on
        pairs.generic_name = vocabulary.gnrc_name
        and pairs.brand_name = vocabulary.brnd_name
