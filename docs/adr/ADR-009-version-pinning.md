# ADR-009: Version pinning across two dbt adapters

## Status

Accepted 2026-09-23. Amended 2026-09-24 (sqlfluff hook, see Decision).

## Context

The project targets two warehouses through dbt: Snowflake via `dbt-snowflake`, and DuckDB via
`dbt-duckdb`. One is a vendor-maintained adapter, the other is community-maintained, and they do
not release in step.

At resolution time, dbt Core was at 1.12.3 (released 21 August 2026) while the most recent
`dbt-duckdb` release was 1.10.1 from February 2026, two minor versions behind. Letting `uv`
resolve the three packages without constraints produced:

```
dbt-core 1.12.5 / dbt-snowflake 1.12.1 / dbt-duckdb 1.11.0
```

Better than the published release dates suggested, but still a cross-minor gap: the DuckDB
adapter trails core by one minor version.

A second version question appeared the next day. The `sqlfluff-lint` pre-commit hook ran in
pre-commit's own isolated virtualenv with `additional_dependencies`, which meant a second,
hand-maintained set of dbt versions sitting alongside `uv.lock` and free to drift from it.

## Decision

**Pin to the highest triple that resolves across both adapters, and treat the community adapter
as the ceiling.** Portability across targets outranks being on the newest minor. Features
introduced in core 1.12 but not supported by `dbt-duckdb` 1.11 are off-limits, because a model
that parses on Snowflake and fails on DuckDB defeats the point of the dual target.

Treat 1.11 as the feature ceiling and the gap never bites.

**Keep exactly one resolved dependency set.** The `sqlfluff-lint` hook was converted from a
versioned remote hook with `additional_dependencies` to a `local` hook running
`uv run sqlfluff lint` from the project venv. The venv already holds dbt-core, both adapters and
the templater at the resolved versions.

The immediate trigger was that the isolated environment installed `dbt-snowflake` only, while CI
lints with `DBT_TARGET=duckdb` because fork pull requests have no Snowflake credentials. Adding
`dbt-duckdb` there would have worked, but the general case is unbounded: every dependency the
templater touches becomes a second thing to keep in sync by hand.

**Pin Terraform providers the same way.** `terraform init` without a version constraint, read
what it resolved, then pin to that. The AWS provider landed on 6.70.0 and `.terraform.lock.hcl`
is committed, exactly as `uv.lock` is.

## Rejected alternatives

**dbt Core 2.0.** Announced June 2026 as a Rust rewrite of Core v1 under Apache 2.0, with
2.0.0rc1 released 2 September 2026. A release candidate three weeks before a project intended to
ship is not a reasonable bet. Worth mentioning in the migration write-up, not worth building on.

**Pin only dbt-core and let the adapters float.** Produces exactly the failure the dual target
exists to prevent: a working Snowflake build and a broken DuckDB one, discovered in CI.

**Drop the DuckDB target to stay current on core.** Would remove the free, portable, runs-after-
the-trial property that ADR-012 later made the architectural thesis.

**Keep `additional_dependencies` and pin them to match `uv.lock`.** The original instinct, and
wrong. It keeps two dependency sets in sync by discipline rather than by construction, and
discipline fails silently.

## Consequences

**A feature ceiling one minor below current.** `vars.yml`, `on_error` model config and
mix-and-match YAML selectors are off the table until the DuckDB adapter catches up. Cheap, since
none of them are needed.

**The linter and the runtime cannot disagree.** One `uv.lock`, one set of versions, in the venv,
in the hook and in CI. The hook now requires `uv` on the machine, which was already true
everywhere it runs.

**A better answer than most.** "Why are you not on the latest dbt?" has a real answer here:
because the community adapter sets the ceiling, and a project that only builds on the paid
target is not portable. That reasoning is the artifact, not the version numbers.
