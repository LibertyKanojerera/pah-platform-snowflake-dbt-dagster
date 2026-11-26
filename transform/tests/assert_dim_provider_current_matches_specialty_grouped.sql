-- Every CMS prescriber has a current version in dim_provider, carrying the
-- same lookup outcome, taxonomy, specialty category and covariate as
-- int_providers__specialty_grouped. The two apply the same rules, one per
-- version and one to the current lookup (S29), so a difference means one
-- has drifted. Driven from the claims' prescribers, so a prescriber with no
-- version fails here too. That includes one whose every lookup failed: it
-- has no history (ADR-023), and its claims could not join a provider.

select
    grp.npi,
    grp.specialty_category as grouped_specialty_category,
    dim.specialty_category as dim_specialty_category,
    grp.is_pulmonary_disease as grouped_is_pulmonary_disease,
    dim.is_pulmonary_disease as dim_is_pulmonary_disease
from {{ ref('int_providers__specialty_grouped') }} as grp
left join {{ ref('dim_provider') }} as dim
    on
        grp.npi = dim.npi
        and dim.is_current
where
    dim.npi is null
    or grp.nppes_found is distinct from dim.nppes_found
    or grp.taxonomy_code is distinct from dim.taxonomy_code
    or grp.specialty_category is distinct from dim.specialty_category
    or grp.is_pulmonary_disease is distinct from dim.is_pulmonary_disease
