-- One row per staged CMS claim row, with is_npi_valid: whether the
-- prescriber NPI passes the CMS check digit (macros/luhn_check.sql).
--
-- Invalid NPIs are flagged, never dropped. valid_npi warns on them in
-- staging (S27), the claims fact keeps them, and the aggregate decides what
-- to count. equal_rowcount holds this model to staging, row for row.

with claims as (
    select * from {{ ref('stg_cms__part_d_claims') }}
)

select
    -- grain
    prescriber_npi,
    brand_name,
    generic_name,
    data_year,

    -- validation: flagged here, never dropped
    {{ luhn_check('prescriber_npi') }} as is_npi_valid,

    -- prescriber as CMS records it; the authority is NPPES
    prescriber_last_org_name,
    prescriber_first_name,
    prescriber_city,
    prescriber_state,
    prescriber_state_fips,
    cms_prescriber_type,
    cms_prescriber_type_source,

    -- volumes
    total_claims,
    total_30day_fills,
    total_day_supply,
    total_drug_cost,
    total_beneficiaries,
    is_beneficiaries_suppressed,

    -- 65 and over
    ge65_suppression_flag,
    ge65_total_claims,
    ge65_total_30day_fills,
    ge65_total_day_supply,
    ge65_total_drug_cost,
    ge65_beneficiaries_suppression_flag,
    ge65_total_beneficiaries,

    -- source metadata and provenance (contract v2, ADR-019)
    _cms_dataset_uuid,
    _source,
    _batch_id,
    _file_name,
    _ingested_at,
    _extracted_at,
    _source_row_hash
from claims
