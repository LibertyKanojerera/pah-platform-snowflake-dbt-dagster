# PAH Analytics Platform: Snowflake / dbt / Dagster

An end-to-end analytics platform for Pulmonary Arterial Hypertension (PAH) specialty
therapeutics, built on AWS with Snowflake, dbt Core and Dagster.

This is a **port**. The same platform exists on Azure Databricks, Delta Lake and Unity Catalog.
Building it a second time on a competing stack is the point: the deliverable is not the pipeline
but the documented comparison between the two.

> **Status: in progress.** Scaffold and tooling are in place. Ingestion, transformation and
> orchestration are being built in sequence. See [`docs/plan.md`](docs/plan.md) for the build
> order and [`docs/session-log.md`](docs/session-log.md) for what has actually shipped.
> Nothing below is claimed as working until it appears in the session log.

## The questions

Two, sitting where commercial analytics meets clinical outcomes:

1. **Prescriber dynamics.** How do provider specialties (pulmonology, cardiology, primary care)
   distribute PAH prescribing across delivery routes (oral, inhaled, subcutaneous/IV), and how
   does that mix shift over time?
2. **Patient persistence.** On a synthetic longitudinal cohort, how long do patients stay on
   therapy, and which factors move the risk of discontinuation? Modeled with Kaplan-Meier curves
   and Cox proportional hazards.

## Architecture (planned)

```
Extract (Python)  ->  Land (S3)  ->  Load (Snowflake)  ->  Transform (dbt)
                                                               |
                                            Orchestrate (Dagster)
                                                               |
                                            Serve (Streamlit + Power BI)
```

Medallion naming is kept in the warehouse (raw, staging/intermediate, marts) so the Databricks
build and this one stay directly comparable.

### Sources

| Source | Type | Why it is here |
|---|---|---|
| CMS Medicare Part D Prescribers by Provider and Drug | Paginated public REST API | Real pagination, real idempotency problem |
| NPPES NPI Registry | Rate-limited enrichment API | Changing provider attributes justify an SCD2 snapshot |
| Synthetic patient events | Local Postgres | Incremental extraction on a high-water mark |

Filtered to the four PAH molecules: treprostinil, selexipag, iloprost, epoprostenol.

## Stack decisions

| Component | Choice | Reasoning |
|---|---|---|
| Cloud | AWS (S3, IAM, Secrets Manager), provisioned with Terraform | Substrate for the warehouse, not a replacement for it. See ADR-011 |
| Warehouse | Snowflake, with a parallel DuckDB target | DuckDB keeps the project free and runnable after the trial, and forces portable SQL |
| Transformation | dbt Core | Repo, profiles and CI stay reviewable |
| Orchestration | Dagster, with an Airflow/Cosmos twin | Dagster's asset model maps onto dbt with no glue. See ADR-008 |
| Extraction | Hand-written Python | Pagination, idempotency and failure recovery are the interesting parts |

The project is built **DuckDB first, Snowflake second**. Everything except the warehouse DDL,
the storage integration and slim CI is warehouse-agnostic, so portability is proven by
construction rather than asserted. See ADR-012.

## Layout

```
infra/terraform/   AWS: landing bucket, IAM, Secrets Manager
infra/snowflake/   Numbered account DDL, run in order
infra/postgres/    Local container for the application source
ingest/            Python ELT: core utilities and one module per source
transform/         dbt project, dual profiles (snowflake, duckdb)
orchestrate/       Dagster assets, resources and schedules
app/               Streamlit dashboard
docs/adr/          Architecture decision records
docs/plan.md       Session-by-session build plan
```

## Running it

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Docker and Terraform.

```bash
uv sync
uv run pre-commit install
cp .env.example .env     # fill in
```

The DuckDB target needs no cloud account and is the default, so `uv run dbt build` from
`transform/` will run the project locally once models exist.

## Data and provenance

All CMS data is public. The synthetic patient cohort is generated locally and contains no real
patient information. The original Databricks build was coursework on the same public dataset;
this port is independent portfolio work and carries nothing proprietary.

## Decision records

Architecture decisions live in [`docs/adr/`](docs/adr/). The ones carried forward from the
Databricks build include the longest-prefix brand-name matcher (naive equality silently dropped
roughly 450 rows) and the beneficiary-suppression handling for a rare disease where 88% of
provider-drug pairs fall below the CMS reporting threshold.
