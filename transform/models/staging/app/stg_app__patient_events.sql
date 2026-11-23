-- Patient events from the application database (Postgres), one row per
-- event, renamed and typed. Two kinds: a fill every 30 days, and one
-- discontinuation for each patient who stopped (days_supply null).
--
-- Events are never updated, but a re-read of the extraction boundary or a
-- full refresh can land one twice, so one row per event_id; the latest
-- landing wins (ADR-021). event_timestamp stays timestamptz. Derive dates
-- in UTC explicitly: the generator writes midnight UTC, and a date cast in
-- a session time zone behind UTC lands a day early.

with landed as (
    select * from {{ source('landing', 'app_postgres_patient_events') }}
),

latest as (
    select *
    from landed
    qualify row_number() over (
        partition by event_id
        order by
            _extracted_at desc,
            _ingested_at desc,
            _batch_id desc
    ) = 1
)

select
    -- key and patient
    event_id,
    patient_id,

    -- event
    nullif(event_type, '') as event_type,
    event_timestamp,
    nullif(route, '') as route,
    cast(days_supply as integer) as days_supply,

    -- source row timestamp
    created_at,

    -- provenance (contract v2, ADR-019)
    _source,
    _batch_id,
    _file_name,
    _ingested_at,
    _extracted_at,
    _source_row_hash
from latest
