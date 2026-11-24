-- The current version of every provider in the history carries the same
-- tracked attributes as staging, which resolves the same landing batches
-- to the latest successful lookup (ADR-021). Two implementations of
-- "latest" must agree; a difference means one of them is wrong. Providers
-- with only failed lookups have no history and are left out.

select
    stg.npi,
    stg.taxonomy_code as staged_taxonomy_code,
    hist.taxonomy_code as history_taxonomy_code
from {{ ref('stg_nppes__providers') }} as stg
left join {{ ref('int_providers__history') }} as hist
    on
        stg.npi = hist.npi
        and hist.valid_to is null
where
    stg.nppes_error is null
    and (
        hist.npi is null
        or stg.nppes_found is distinct from hist.nppes_found
        or stg.taxonomy_code is distinct from hist.taxonomy_code
        or stg.location_state is distinct from hist.location_state
        or stg.location_postal_code is distinct from hist.location_postal_code
    )
