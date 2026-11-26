-- One row per provider version (SCD2), from int_providers__history
-- (ADR-023), with research question one's specialty category and research
-- question two's covariate resolved for each version.
--
-- Two ranges per version. valid_from and valid_to are what was observed:
-- from the first lookup that saw this version to the first that saw the
-- next. effective_from and effective_to are what facts join on: the same,
-- except that a provider's first version reaches back to 1900-01-01 and
-- its current version forward to 9999-12-31, so every instant falls in
-- exactly one version. A fact dated before valid_from has joined a
-- backfilled version. The facts carry that flag (S32, S33), because it
-- describes the join, not the provider.
--
-- specialty_category and is_pulmonary_disease repeat the rules in
-- int_providers__specialty_grouped, applied per version instead of to the
-- current lookup. assert_dim_provider_current_matches_specialty_grouped
-- holds the two implementations to the same answer.

with history as (
    select * from {{ ref('int_providers__history') }}
),

specialty_map as (
    select * from {{ ref('specialty_category_map') }}
)

select
    {{ dbt_utils.generate_surrogate_key(['history.npi', 'history.version_number']) }} as provider_key,
    history.npi,
    history.version_number,
    history.nppes_found,
    history.taxonomy_code,
    specialty_map.taxonomy_desc,
    case
        when history.nppes_found then specialty_map.specialty_category
        when not history.nppes_found then 'unknown'
    end as specialty_category,
    coalesce(history.taxonomy_code, '') = '207RP1001X' as is_pulmonary_disease,
    history.location_state,
    history.location_postal_code,
    history.valid_from,
    history.valid_to,
    history.valid_to is null as is_current,
    case
        when history.version_number = 1
            then cast('1900-01-01 00:00:00+00:00' as timestamp with time zone)
        else history.valid_from
    end as effective_from,
    coalesce(
        history.valid_to,
        cast('9999-12-31 00:00:00+00:00' as timestamp with time zone)
    ) as effective_to
from history
left join specialty_map
    on history.taxonomy_code = specialty_map.taxonomy_code
