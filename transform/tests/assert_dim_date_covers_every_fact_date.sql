-- Every date a fact can carry is in dim_date: the first and last day of
-- each CMS data year, each patient's index date and each patient event's
-- date. dim_date's range is fixed (2013 to 2026), so this is the test that
-- notices a CMS release or an event outside it. The session runs in UTC
-- (S27), so the event date is a UTC date.

with fact_dates as (
    select cast(cast(data_year as varchar) || '-01-01' as date) as fact_date
    from {{ ref('stg_cms__part_d_claims') }}
    union
    select cast(cast(data_year as varchar) || '-12-31' as date) as fact_date
    from {{ ref('stg_cms__part_d_claims') }}
    union
    select index_date as fact_date
    from {{ ref('stg_app__patients') }}
    union
    select cast(event_timestamp as date) as fact_date
    from {{ ref('stg_app__patient_events') }}
)

select fact_dates.fact_date
from fact_dates
left join {{ ref('dim_date') }} as dim_date
    on fact_dates.fact_date = dim_date.date_day
where dim_date.date_day is null
