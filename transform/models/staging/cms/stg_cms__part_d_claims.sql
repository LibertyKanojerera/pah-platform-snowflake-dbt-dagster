-- CMS Part D prescribing, one row per prescriber, brand, generic and data
-- year, renamed and typed. No rows are filtered: accessories are flagged
-- by product_type in the intermediate layer and left out where claims are
-- counted (ADR-022).
--
-- The landing zone is append-only. A CMS release is a full snapshot of its
-- year, so the latest landed batch of each year wins whole; taking the
-- latest row per key instead would keep rows a restatement removed. With
-- one batch per year today this is a no-op, proved by unit test at S26.

with landed as (
    select * from {{ source('landing', 'cms_part_d') }}
),

batches as (
    select
        _cms_release_year,
        _batch_id,
        max(_extracted_at) as batch_extracted_at,
        max(_ingested_at) as batch_ingested_at
    from landed
    group by _cms_release_year, _batch_id
),

latest_batches as (
    select
        _cms_release_year,
        _batch_id
    from batches
    qualify row_number() over (
        partition by _cms_release_year
        order by
            batch_extracted_at desc,
            batch_ingested_at desc,
            _batch_id desc
    ) = 1
),

current_release as (
    select landed.*
    from landed
    inner join latest_batches
        on
            landed._cms_release_year = latest_batches._cms_release_year
            and landed._batch_id = latest_batches._batch_id
)

select
    -- grain
    prscrbr_npi as prescriber_npi,
    brnd_name as brand_name,
    gnrc_name as generic_name,
    cast(_cms_release_year as integer) as data_year,

    -- prescriber as CMS records it; the authority is NPPES
    nullif(prscrbr_last_org_name, '') as prescriber_last_org_name,
    nullif(prscrbr_first_name, '') as prescriber_first_name,
    nullif(prscrbr_city, '') as prescriber_city,
    nullif(prscrbr_state_abrvtn, '') as prescriber_state,
    nullif(prscrbr_state_fips, '') as prescriber_state_fips,
    nullif(prscrbr_type, '') as cms_prescriber_type,
    nullif(prscrbr_type_src, '') as cms_prescriber_type_source,

    -- volumes: never suppressed in this dataset, so a blank fails the cast
    cast(tot_clms as integer) as total_claims,
    cast(tot_30day_fills as decimal(18, 2)) as total_30day_fills,
    cast(tot_day_suply as integer) as total_day_supply,
    cast(tot_drug_cst as decimal(18, 2)) as total_drug_cost,

    -- beneficiaries: blank means 1 to 10, suppressed by CMS
    cast(nullif(tot_benes, '') as integer) as total_beneficiaries,
    coalesce(tot_benes, '') = '' as is_beneficiaries_suppressed,

    -- 65 and over: a flag (* or #) blanks the values it covers
    nullif(ge65_sprsn_flag, '') as ge65_suppression_flag,
    cast(nullif(ge65_tot_clms, '') as integer) as ge65_total_claims,
    cast(
        nullif(ge65_tot_30day_fills, '') as decimal(18, 2)
    ) as ge65_total_30day_fills,
    cast(nullif(ge65_tot_day_suply, '') as integer) as ge65_total_day_supply,
    cast(
        nullif(ge65_tot_drug_cst, '') as decimal(18, 2)
    ) as ge65_total_drug_cost,
    nullif(ge65_bene_sprsn_flag, '') as ge65_beneficiaries_suppression_flag,
    cast(nullif(ge65_tot_benes, '') as integer) as ge65_total_beneficiaries,

    -- source metadata and provenance (contract v2, ADR-019)
    _cms_dataset_uuid,
    _source,
    _batch_id,
    _file_name,
    _ingested_at,
    _extracted_at,
    _source_row_hash
from current_release
