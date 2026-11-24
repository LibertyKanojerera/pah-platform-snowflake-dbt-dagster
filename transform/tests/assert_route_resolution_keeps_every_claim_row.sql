-- Route resolution must not lose a claim row: the rows of every drug that
-- resolved add up to every row in staging. ADR-002's 450 Orenitram rows,
-- kept as a permanent check (ADR-022). Returns one row, with both counts,
-- when they differ.

with resolved as (
    select coalesce(sum(claim_rows), 0) as row_count
    from {{ ref('int_drugs__route_resolved') }}
    where route is not null
),

staged as (
    select count(*) as row_count
    from {{ ref('stg_cms__part_d_claims') }}
)

select
    resolved.row_count as resolved_rows,
    staged.row_count as staged_rows
from resolved
cross join staged
where resolved.row_count <> staged.row_count
