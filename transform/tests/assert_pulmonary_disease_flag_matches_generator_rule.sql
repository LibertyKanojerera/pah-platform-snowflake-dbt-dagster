-- is_pulmonary_disease, research question two's prescriber covariate, must
-- match the rule infra/postgres/generate.py used to build the cohort's
-- ground truth: an NPPES taxonomy description containing "pulmonary". The
-- model keys on the code (207RP1001X) because NUCC rewords descriptions.
-- This test fails the day the two rules disagree, for example when a
-- pediatric pulmonology code appears, so the covariate is then decided
-- deliberately instead of drifting from the coefficients S46 must recover.

select
    npi,
    taxonomy_code,
    taxonomy_desc,
    is_pulmonary_disease
from {{ ref('int_providers__specialty_grouped') }}
where is_pulmonary_disease <> (coalesce(taxonomy_desc, '') ilike '%pulmonary%')
