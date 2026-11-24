-- Every provider in the history has exactly one current version (valid_to
-- null). Two would be two simultaneous truths; none would leave the
-- provider with no current state.

select
    npi,
    sum(case when valid_to is null then 1 else 0 end) as current_versions
from {{ ref('int_providers__history') }}
group by npi
having sum(case when valid_to is null then 1 else 0 end) <> 1
