-- One row per prescriber in the CMS claims: the NPPES lookup outcome, the
-- primary taxonomy, research question one's specialty category
-- (specialty_category_map, S24) and research question two's prescriber
-- covariate.
--
-- Driven from the claims, not from NPPES, so a prescriber that was never
-- looked up still has a row, with a null nppes_found that fails not_null.
-- A prescriber NPPES did not find is 'unknown', an explicit state, so its
-- claims stay visible downstream. A found prescriber whose taxonomy is
-- unmapped keeps a null category and fails not_null: there is no default.
--
-- is_pulmonary_disease is deliberately narrower than the pulmonology
-- category. It is the covariate infra/postgres/generate.py built the
-- cohort's ground truth from (taxonomy description containing
-- "pulmonary", which here is exactly 207RP1001X), so S46 can recover the
-- designed coefficient. Critical care and sleep medicine are pulmonology
-- for research question one and not for research question two.

with prescribers as (
    select distinct prescriber_npi as npi
    from {{ ref('stg_cms__part_d_claims') }}
),

providers as (
    select * from {{ ref('stg_nppes__providers') }}
),

specialty_map as (
    select * from {{ ref('specialty_category_map') }}
)

select
    prescribers.npi,
    providers.nppes_found,
    providers.taxonomy_code,
    providers.taxonomy_desc,
    providers.is_taxonomy_ambiguous,
    case
        when providers.nppes_found then specialty_map.specialty_category
        when not providers.nppes_found then 'unknown'
    end as specialty_category,
    coalesce(providers.taxonomy_code, '') = '207RP1001X' as is_pulmonary_disease
from prescribers
left join providers
    on prescribers.npi = providers.npi
left join specialty_map
    on providers.taxonomy_code = specialty_map.taxonomy_code
