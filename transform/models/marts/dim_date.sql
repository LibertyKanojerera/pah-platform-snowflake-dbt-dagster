-- One row per calendar day, 2013-01-01 to 2026-12-31: every CMS data year
-- landed (2013 to 2024), every patient event (2019 to 2023) and the next
-- two CMS releases. The range is fixed rather than derived from the facts,
-- so the dimension is the same on every run, and
-- assert_dim_date_covers_every_fact_date fails when a fact falls outside
-- it. date_spine's end date is exclusive, hence 2027-01-01.
--
-- Attributes use extract() and case, which DuckDB and Snowflake share. Day
-- of week and ISO week are left out until a model needs them: the two
-- engines spell them differently.

with days as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('2013-01-01' as date)",
        end_date="cast('2027-01-01' as date)"
    ) }}
),

calendar as (
    select cast(date_day as date) as date_day
    from days
)

select
    date_day,
    cast(extract(year from date_day) as integer) as calendar_year,
    cast(extract(quarter from date_day) as integer) as calendar_quarter,
    cast(extract(month from date_day) as integer) as calendar_month,
    case cast(extract(month from date_day) as integer)
        when 1 then 'January'
        when 2 then 'February'
        when 3 then 'March'
        when 4 then 'April'
        when 5 then 'May'
        when 6 then 'June'
        when 7 then 'July'
        when 8 then 'August'
        when 9 then 'September'
        when 10 then 'October'
        when 11 then 'November'
        when 12 then 'December'
    end as month_name,
    cast(extract(day from date_day) as integer) as day_of_month
from calendar
