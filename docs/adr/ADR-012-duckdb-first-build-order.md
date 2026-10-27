# ADR-012: DuckDB-first build order

## Status

Accepted 2026-10-23. Supersedes the 17-working-day schedule in the original port spec.

## Context

The original plan assumed full working days: roughly 100 hours across 17 days, plus 18 hours for
the AWS additions in ADR-011. Available capacity is two hours per day, of which perhaps ninety
minutes is productive once context is reloaded. That is 65 to 75 sessions, or ten to fourteen
weeks depending on sessions per week.

The Snowflake trial is 30 **calendar** days and cannot be paused. At this cadence the trial
spans roughly fifteen full-day equivalents, against the twenty the plan needs. Starting the
clock at the beginning means it expires midway through the dbt work, with nothing shipped,
no public artifact, and no warehouse.

The original ordering put Snowflake first because the spec was written assuming full days. That
assumption no longer holds, so the ordering has to change.

## Decision

Build the platform **DuckDB first and Snowflake last**. Register the Snowflake account only when
everything that does not require it already works.

Phases, by work unit rather than date:

| Phase | Work | Snowflake clock |
|---|---|---|
| 0 | AWS account, Terraform, S3, IAM | not started |
| 1 | Three extractors landing Parquet to S3, read back via DuckDB | not started |
| 2 | dbt: staging through marts, SCD2 snapshot, tests, docs | not started |
| 3 | Dagster, Streamlit, GitHub Actions, Pages | not started |
| 4 | Snowflake port: DDL, storage integration, slim CI, Time Travel | **running** |
| 5 | Airflow twin, Elementary, migration write-up | expired, irrelevant |

Phase 4 has an entry condition rather than a start date: do not begin it unless twelve sessions
are reasonably protected. Twelve sessions at five per week is seventeen days, inside the trial
with margin. At three per week it is four weeks and the trial expires mid-phase.

## Rejected alternatives

**Original ordering, Snowflake first.** Trial expires around the midpoint of Phase 2. Leaves an
expired warehouse, a half-built dbt project and nothing public.

**Pay for Snowflake after the trial.** Smallest viable spend is far above the under-$5-per-month
figure the cost and CI resume bullet claims, which would make that bullet false.

**Shrink scope to fit the trial.** Cutting to what fits in fifteen full-day equivalents means
dropping either the SCD2 snapshot, the orchestration layer or the three-source ingestion, each
of which underpins a resume bullet. The cut list in `docs/plan.md` exists for genuine schedule
pressure; using it to accommodate a trial clock is the wrong trade.

## Consequences

**A public artifact exists before the warehouse does.** At the end of Phase 3 the repo is public
and runnable, the Streamlit dashboard is live on a permanent URL, dbt docs are hosted, and three
of five resume bullets are true. Job applications start there, not at the end.

**Interruption tolerance.** The original ordering had no safe stopping point until the end. This
one has one at the end of every phase, which matters given that an eight-day and then a
nineteen-day gap have already occurred.

**Portability becomes the thesis rather than a footnote.** "Built portable from the start, then
proved it by adding a second warehouse" is a stronger and more honest claim than "built on
Snowflake, added DuckDB to save money on CI." The dual-target decision from the original spec
was always sound; this ordering makes it load-bearing.

**Dialect divergence surfaces late.** The known risk. Every model is written and tested against
DuckDB before Snowflake ever sees it, so divergence arrives in one block in Phase 4 rather than
being caught model by model. Mitigated by reserving two sessions (S59 and S60) for it, by ANSI
plain SQL as the default, and by dispatched macros where the adapters genuinely differ. If it
takes one session, the other is banked.

**Snowflake-specific work is concentrated and cannot slip.** Zero-copy clone CI and the Time
Travel drill only exist while the trial is live. They sit in Phase 4 and must not drift into
buffer.
