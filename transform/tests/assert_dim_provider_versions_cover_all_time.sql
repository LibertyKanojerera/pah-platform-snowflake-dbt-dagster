-- Every provider's versions, end to end, run from 1900-01-01 to 9999-12-31.
-- With mutually_exclusive_ranges (no gaps, no overlaps) this means every
-- instant falls in exactly one version, so a fact of any date joins to one
-- provider row: none dropped, none duplicated.

select
    npi,
    min(effective_from) as first_effective_from,
    max(effective_to) as last_effective_to
from {{ ref('dim_provider') }}
group by npi
having
    min(effective_from) <> cast('1900-01-01 00:00:00+00:00' as timestamp with time zone)
    or max(effective_to) <> cast('9999-12-31 00:00:00+00:00' as timestamp with time zone)
