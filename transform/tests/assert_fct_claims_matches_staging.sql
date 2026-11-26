-- The fact holds exactly the staged claims: every staged row once, with
-- the batch and source row hash staging holds now, and nothing staging
-- does not hold. Incremental loads touch only release years with a new
-- batch (ADR-024), so this is what notices a year left stale, or a row a
-- restatement dropped that survived the load. A row count would miss one
-- row swapped for another.

select
    coalesce(stg.prescriber_npi, fct.prescriber_npi) as prescriber_npi,
    coalesce(stg.generic_name, fct.generic_name) as generic_name,
    coalesce(stg.brand_name, fct.brand_name) as brand_name,
    coalesce(stg.data_year, fct.data_year) as data_year,
    stg._batch_id as staged_batch_id,
    fct._batch_id as fact_batch_id
from {{ ref('stg_cms__part_d_claims') }} as stg
full outer join {{ ref('fct_claims') }} as fct
    on
        stg.prescriber_npi = fct.prescriber_npi
        and stg.generic_name = fct.generic_name
        and stg.brand_name = fct.brand_name
        and stg.data_year = fct.data_year
where
    stg.prescriber_npi is null
    or fct.prescriber_npi is null
    or stg._batch_id is distinct from fct._batch_id
    or stg._source_row_hash is distinct from fct._source_row_hash
