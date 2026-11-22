-- NPPES enrichment, one row per NPI ever looked up, renamed and typed.
--
-- The row kept is the latest successful lookup: a hit, or a definitive
-- miss. A failure (nppes_error set) is never cached and is retried on the
-- next run, so it never displaces a successful row; it survives only for
-- an NPI with no successful lookup yet. One batch today, so this is a
-- no-op, proved by unit test at S26.
--
-- Misses stay, with nppes_found false and the rest null: CMS prescribing
-- is the fact and NPPES the dimension. _nppes_raw_response stays in
-- landing; extract from it here only when a model needs a field that
-- flattening omits.

with landed as (
    select * from {{ source('landing', 'nppes') }}
),

latest as (
    select *
    from landed
    qualify row_number() over (
        partition by npi
        order by
            case when coalesce(nppes_error, '') = '' then 0 else 1 end,
            _extracted_at desc,
            _ingested_at desc,
            _batch_id desc
    ) = 1
)

select
    -- key and lookup outcome
    npi,
    nppes_found,
    nullif(nppes_error, '') as nppes_error,

    -- identity
    nullif(enumeration_type, '') as enumeration_type,
    nullif(status, '') as npi_status,
    nullif(credential, '') as credential,
    nullif(name_prefix, '') as name_prefix,
    nullif(first_name, '') as first_name,
    nullif(middle_name, '') as middle_name,
    nullif(last_name, '') as last_name,
    nullif(organization_name, '') as organization_name,
    nullif(sole_proprietor, '') as sole_proprietor,

    -- dates arrive as ISO strings
    cast(nullif(enumeration_date, '') as date) as enumeration_date,
    cast(nullif(last_updated, '') as date) as last_updated_date,
    cast(nullif(certification_date, '') as date) as certification_date,

    -- primary taxonomy, the specialty key (specialty_category_map)
    nullif(taxonomy_code, '') as taxonomy_code,
    nullif(taxonomy_desc, '') as taxonomy_desc,
    nullif(taxonomy_license, '') as taxonomy_license,
    nullif(taxonomy_state, '') as taxonomy_state,
    taxonomy_count,
    taxonomy_ambiguous as is_taxonomy_ambiguous,

    -- primary practice location (the LOCATION address, not MAILING)
    nullif(location_address_1, '') as location_address_1,
    nullif(location_city, '') as location_city,
    nullif(location_state, '') as location_state,
    nullif(location_postal_code, '') as location_postal_code,
    nullif(location_postal_code_full, '') as location_postal_code_full,
    location_missing as is_location_missing,

    -- provenance (contract v2, ADR-019)
    _source,
    _batch_id,
    _file_name,
    _ingested_at,
    _extracted_at,
    _source_row_hash
from latest
