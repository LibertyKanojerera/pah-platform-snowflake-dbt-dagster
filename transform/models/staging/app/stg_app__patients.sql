-- Synthetic PAH cohort from the application database (Postgres), one row
-- per patient, renamed and typed.
--
-- Every extraction can land a patient again, so the row kept is the latest
-- version by the source's own updated_at, with extraction time as the
-- tiebreak (ADR-021). Postgres hard deletes are not captured: the table
-- has no deleted flag, so a deleted patient keeps its last landed row.

with landed as (
    select * from {{ source('landing', 'app_postgres_patients') }}
),

latest as (
    select *
    from landed
    qualify row_number() over (
        partition by patient_id
        order by
            updated_at desc,
            _extracted_at desc,
            _ingested_at desc,
            _batch_id desc
    ) = 1
)

select
    -- key and prescriber
    patient_id,
    nullif(prescriber_npi, '') as prescriber_npi,

    -- cohort entry
    index_date,
    nullif(initial_route, '') as initial_route,
    cast(copay_tier as integer) as copay_tier,

    -- demographics
    nullif(age_band, '') as age_band,
    nullif(sex, '') as sex,
    nullif(prescriber_state, '') as prescriber_state,

    -- source row timestamps
    created_at,
    updated_at,

    -- provenance (contract v2, ADR-019)
    _source,
    _batch_id,
    _file_name,
    _ingested_at,
    _extracted_at,
    _source_row_hash
from latest
