{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='data_year',
        on_schema_change='append_new_columns'
    )
}}

-- Claims fact: one row per prescriber, generic, brand and data year, as
-- CMS reports it, keyed to the provider version, the drug and the date it
-- joins on. Every staged row is kept: an invalid NPI is flagged
-- (is_npi_valid), and accessories are left to the aggregate (plan S34).
--
-- Incremental by release year (ADR-024). A CMS release is a full snapshot
-- of its year (ADR-021), so a year is loaded only when staging holds a
-- batch for it that the fact has not loaded, and then replaced whole:
-- delete+insert on data_year removes any row a restatement dropped, where
-- a row-level merge would keep it. A year whose batch is unchanged is not
-- touched, and _loaded_at shows when each year last loaded.
--
-- The provider is the dim_provider version in effect on the last day of
-- the data year. Every claim year predates the NPPES history (ADR-023), so
-- today every row joins a backfilled first version and says so in
-- is_provider_backfilled. CMS's prescriber type and state for the year
-- stay on the row as the year-accurate values.
--
-- Dimensions are joined when a year loads. A later dimension change
-- reaches years already loaded only on --full-refresh.

with claims as (
    select * from {{ ref('int_claims__npi_validated') }}
),

{% if is_incremental() %}
    loaded_batches as (
        select distinct
            data_year,
            _batch_id
        from {{ this }}
    ),
{% endif %}

claims_to_load as (
    select
        claims.*,
        cast(cast(claims.data_year as varchar) || '-12-31' as date) as data_year_end
    from claims
    {% if is_incremental() %}
        left join loaded_batches
            on
                claims.data_year = loaded_batches.data_year
                and claims._batch_id = loaded_batches._batch_id
        where loaded_batches._batch_id is null
    {% endif %}
),

providers as (
    select * from {{ ref('dim_provider') }}
),

drugs as (
    select * from {{ ref('dim_drug') }}
)

select
    -- keys
    {{ dbt_utils.generate_surrogate_key([
        'claims_to_load.prescriber_npi',
        'claims_to_load.generic_name',
        'claims_to_load.brand_name',
        'claims_to_load.data_year'
    ]) }} as claim_key,
    providers.provider_key,
    drugs.drug_key,
    claims_to_load.data_year,
    claims_to_load.data_year_end,

    -- grain, as CMS reports it
    claims_to_load.prescriber_npi,
    claims_to_load.generic_name,
    claims_to_load.brand_name,

    -- flags
    claims_to_load.is_npi_valid,
    claims_to_load.data_year_end < providers.valid_from as is_provider_backfilled,

    -- the prescriber as CMS recorded it that year (ADR-023)
    claims_to_load.cms_prescriber_type,
    claims_to_load.prescriber_state,

    -- volumes
    claims_to_load.total_claims,
    claims_to_load.total_30day_fills,
    claims_to_load.total_day_supply,
    claims_to_load.total_drug_cost,
    claims_to_load.total_beneficiaries,
    claims_to_load.is_beneficiaries_suppressed,

    -- 65 and over
    claims_to_load.ge65_suppression_flag,
    claims_to_load.ge65_total_claims,
    claims_to_load.ge65_total_30day_fills,
    claims_to_load.ge65_total_day_supply,
    claims_to_load.ge65_total_drug_cost,
    claims_to_load.ge65_beneficiaries_suppression_flag,
    claims_to_load.ge65_total_beneficiaries,

    -- source metadata and provenance (contract v2, ADR-019)
    claims_to_load._cms_dataset_uuid,
    claims_to_load._source,
    claims_to_load._batch_id,
    claims_to_load._file_name,
    claims_to_load._ingested_at,
    claims_to_load._extracted_at,
    claims_to_load._source_row_hash,
    cast('{{ run_started_at }}' as timestamp with time zone) as _loaded_at
from claims_to_load
left join providers
    on
        claims_to_load.prescriber_npi = providers.npi
        and claims_to_load.data_year_end >= providers.effective_from
        and claims_to_load.data_year_end < providers.effective_to
left join drugs
    on
        claims_to_load.generic_name = drugs.generic_name
        and claims_to_load.brand_name = drugs.brand_name
