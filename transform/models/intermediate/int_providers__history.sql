{{ config(materialized='table') }}

-- Provider history (SCD2): one row per version of the attributes tracked
-- over time, namely whether NPPES finds the NPI, its primary taxonomy code
-- and its practice location (state and 5-digit ZIP). Derived from every
-- NPPES batch in the landing zone rather than captured by a dbt snapshot
-- (ADR-023): the landing zone is already the full log, so the history is
-- rebuildable anywhere and its logic is unit-tested.
--
-- An observation is one successful lookup, a hit or a miss. A failed
-- lookup says nothing about the provider and is ignored, the rule staging
-- applies (ADR-021). Cache re-landings repeat an observation and collapse
-- to one. A version starts wherever a tracked attribute differs from the
-- previous observation, so A, B, A is three versions. valid_from is when a
-- version was first observed, not when NPPES recorded the change, and the
-- history starts at the first lookup in November 2026.
--
-- Materialised as a table, not ephemeral like the other intermediates:
-- downstream joins on validity ranges read it repeatedly, and recomputing
-- it from S3 each time is wasteful.

with landed as (
    select * from {{ source('landing', 'nppes') }}
),

observations as (
    select
        npi,
        _extracted_at as observed_at,
        nppes_found,
        nullif(taxonomy_code, '') as taxonomy_code,
        nullif(location_state, '') as location_state,
        nullif(location_postal_code, '') as location_postal_code
    from landed
    where coalesce(nppes_error, '') = ''
    qualify row_number() over (
        partition by npi, _extracted_at
        order by _ingested_at desc, _batch_id desc
    ) = 1
),

with_previous as (
    select
        npi,
        observed_at,
        nppes_found,
        taxonomy_code,
        location_state,
        location_postal_code,
        row_number() over (
            partition by npi order by observed_at
        ) as observation_number,
        lag(nppes_found) over (
            partition by npi order by observed_at
        ) as previous_nppes_found,
        lag(taxonomy_code) over (
            partition by npi order by observed_at
        ) as previous_taxonomy_code,
        lag(location_state) over (
            partition by npi order by observed_at
        ) as previous_location_state,
        lag(location_postal_code) over (
            partition by npi order by observed_at
        ) as previous_postal_code
    from observations
),

flagged as (
    select
        npi,
        observed_at,
        nppes_found,
        taxonomy_code,
        location_state,
        location_postal_code,
        case
            when observation_number = 1 then 1
            when (previous_nppes_found is distinct from nppes_found) then 1
            when (previous_taxonomy_code is distinct from taxonomy_code) then 1
            when (previous_location_state is distinct from location_state) then 1
            when (previous_postal_code is distinct from location_postal_code) then 1
            else 0
        end as is_change
    from with_previous
),

numbered as (
    select
        npi,
        observed_at,
        nppes_found,
        taxonomy_code,
        location_state,
        location_postal_code,
        is_change,
        cast(sum(is_change) over (
            partition by npi
            order by observed_at
            rows between unbounded preceding and current row
        ) as integer) as version_number
    from flagged
),

version_spans as (
    select
        npi,
        version_number,
        max(observed_at) as last_observed_at,
        count(*) as observations
    from numbered
    group by npi, version_number
),

version_starts as (
    select
        npi,
        version_number,
        observed_at as valid_from,
        nppes_found,
        taxonomy_code,
        location_state,
        location_postal_code
    from numbered
    where is_change = 1
)

select
    version_starts.npi,
    version_starts.version_number,
    version_starts.nppes_found,
    version_starts.taxonomy_code,
    version_starts.location_state,
    version_starts.location_postal_code,
    version_starts.valid_from,
    lead(version_starts.valid_from) over (
        partition by version_starts.npi
        order by version_starts.version_number
    ) as valid_to,
    version_spans.last_observed_at,
    version_spans.observations
from version_starts
inner join version_spans
    on
        version_starts.npi = version_spans.npi
        and version_starts.version_number = version_spans.version_number
