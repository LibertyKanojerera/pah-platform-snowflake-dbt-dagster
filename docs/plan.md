# Build Plan: PAH Platform Port to Snowflake / dbt / Dagster

*Session-numbered, not date-numbered. Pace: two-hour sessions. 70 sessions total.*
*Supersedes the 17-day schedule in the port spec. See ADR-012 for why.*

## How to use this file

One session, one objective, one commit. Never stop mid-refactor; if a session will not fit,
split it before starting rather than when the clock runs out.

End every session by appending to `docs/session-log.md`:

```
## S14 - 2026-11-06
Did: NPPES response shape documented, sample saved to data/samples/nppes_sample.json
Next: add token-bucket throttle to ingest/sources/nppes.py, 20 req/sec, test the dead-letter path
```

The `Next:` line is the single highest-value habit in this plan. Without it you lose the first
thirty minutes of each session rediscovering context, which is a quarter of your time.

Sessions are grouped by tool, not by layer, so you reload one mental model per stretch
rather than three per week.

**Numbering offset.** The session log counts calendar sessions; this file counts work units.
They diverged early (log S4 covered plan S3, and so on) because setup overran. Go by the `Next:`
line in the log, which always names the plan item.

## Build order, and the one thing that changed

The platform is built **DuckDB first, Snowflake last**. Everything except the warehouse DDL,
the storage integration, slim CI, the Time Travel drill and the Power BI repoint is
warehouse-agnostic. At two hours a day the 30-day Snowflake trial cannot span the whole build,
so it is spent only on Phase 4, where it is unavoidable.

This is an improvement, not a rescue. "Built portable from the start, then proved it by adding a
second warehouse" is a stronger claim than "built on Snowflake, added DuckDB for CI." The
dual-target decision becomes the architectural thesis rather than a cost footnote.

**You have a complete, public, working artifact at the end of Phase 3, before the trial starts.**

## Phase gates

| Gate | After | Condition |
|---|---|---|
| G1 | S6 | `terraform apply` clean; GitHub Actions assumes an AWS role with no stored keys |
| G2 | S21 | All three sources land Parquet to S3 idempotently; repeated-run test green |
| G3 | S37 | `dbt build` green on DuckDB; docs generate; exposures resolve |
| G4 | S51 | Public repo, live Streamlit URL, hosted dbt docs. **Start applying to roles here.** |
| G5 | S63 | `dbt build` green on Snowflake; slim CI runs on a zero-copy clone |
| G6 | S70 | Migration write-up published; ADR log complete |

---

## Phase 0: AWS and Terraform foundations (S1 to S6)

**S1. Account and guardrails.** DONE.
Classic AWS account via "Sign up for AWS (advanced)", root MFA, IAM access to billing activated,
$5 budget with 80% actual and 100% forecast alerts, us-east-1. IAM Identity Center with a
4-hour `AdministratorAccess` permission set rather than an IAM user with a long-lived access key,
for consistency with S5 and S6. AWS CLI v2 and Terraform installed.
*A first attempt via "Sign up for AWS (new)" produced a Builder ID account with no root user and
invisible SCPs; abandoned. See ADR-011.*

**S2. Terraform skeleton and remote state.** DONE.
`infra/terraform/` with provider pinned to the resolved version (`hashicorp/aws 6.70.0`), and an
S3 state bucket using native S3 state locking (`use_lockfile = true`) rather than a DynamoDB
table. Bootstrapped with local state, then migrated. `.terraform.lock.hcl` committed.

**S3. Landing bucket.** DONE.
Versioning, SSE-S3, all four public access blocks, 90-day expiry on `landing/`, 30-day
noncurrent expiry, incomplete multipart uploads aborted at 7 days. Key layout documented in
`docs/architecture.md`.
*Amendment: written as plain resources, not a module. Two buckets that differ in exactly the
ways that matter (`prevent_destroy` against lifecycle rules) need a toggle variable per
difference, which reads worse than the duplication. Extract if a third bucket appears.*

**S4. IAM role for Snowflake.** DONE.
Role `pah-platform-snowflake-storage-integration`, inline policy read-scoped to the `landing/`
prefix with an `s3:prefix` condition on `ListBucket`. `snowflake_iam_user_arn` and
`snowflake_external_id` are null variables, so S56 is a tfvars change and an apply rather than a
code edit. Trust policy currently names the account root and carries no `sts:ExternalId`
condition; that is the expected pre-S56 state.

**S5. Secrets Manager.**
One secret for the Snowflake key pair (populated at S55), one for Postgres credentials.
Secret *containers* in Terraform, secret *values* never in Terraform, because
`aws_secretsmanager_secret_version` writes the plaintext into state. `ingest/core/config.py`
resolves from Secrets Manager when `AWS_PROFILE` is set and from `.env` otherwise. Retires the
base64-encoded-PEM workaround entirely.
*Commit: secrets module, `ingest/core/config.py`.*

**S6. GitHub Actions OIDC. GATE G1.**
OIDC identity provider and an IAM role trusting this repo only, scoped to `main` and pull
requests. Smoke workflow that assumes the role and lists the bucket. No long-lived AWS keys in
GitHub secrets, ever.
*Commit: `.github/workflows/aws-smoke.yml`, green run.*

---

## Phase 1: Ingestion (S7 to S21)

**S7. HTTP core.** `ingest/core/http.py`: session with retry, exponential backoff, jitter via
tenacity; structlog configuration; typed response wrapper.
**S8. Landing core.** `ingest/core/landing.py`: Parquet writer, batch UUID, partitioned key
builder, manifest sidecar, S3 upload.
**S9. State core.** `ingest/core/state.py`: checkpoint read and write, against a local DuckDB
table now and `META.INGESTION_STATE` later. Written only after a successful load, which is the
correct version of the Auto Loader stale-checkpoint bug.
**S10. CMS reconnaissance.** Hit `data.cms.gov` by hand. Document the response envelope,
pagination parameters and filter syntax in `docs/sources.md`. Save a sample to
`data/samples/`. Do not write extractor code this session.
**S11. CMS pagination.** `cms_part_d.py`: the offset and size loop, total-count handling,
termination conditions.
**S12. CMS landing.** Server-side filter to the four molecules, provenance columns
(`_source`, `_batch_id`, `_file_name`, `_ingested_at`, `_source_row_hash`), write to S3.
**S13. Idempotency test.** pytest: run twice, assert identical row counts and no duplicate
natural keys. This is the test the ingestion resume bullet rests on, so it must be real.
**S14. NPPES reconnaissance.** Response shape, 404 behaviour, observed rate limits. Sample saved.
**S15. NPPES extractor.** Batched NPI lookup with a token-bucket throttle.
**S16. NPPES resilience.** Disk cache, dead-letter table, failure treated as nullable enrichment
rather than pipeline failure.
**S17. Synthetic events.** Finish `infra/postgres/seed.sql` and the generator (both are currently
empty placeholders). 5,000 patients, event timestamps, copay tier, initial route, prescriber NPI
drawn from the CMS set so the join actually resolves.
**S18. Postgres extractor.** Incremental extraction on an `event_timestamp` high-water mark.
**S19. CLI.** `ingest/cli.py`: one entry point, three subcommands, `--full-refresh` flag,
`--dry-run`.
**S20. Observability.** Structured logging to an ingestion log table: rows read and written,
duration, batch id. Audit provenance columns across all three sources for consistency.
**S21. GATE G2.** Clean run from zero: all three sources land to S3, DuckDB reads every landed
Parquet via `httpfs`, idempotency test green, logs populated.
*The ingestion resume bullet is now true. Do not write it yet; S70.*

---

## Phase 2: dbt (S22 to S37)

**S22. Project config.** Layer materialisations in `dbt_project.yml`, `dbt debug` green on the
duckdb target. First real exercise of the sqlfluff hook, which has been a no-op since September.
**S23. Sources.** `_sources.yml` per source with freshness thresholds, reading the S3 Parquet
through DuckDB `httpfs`.
**S24. Seeds.** `drug_route_reference.csv`, `specialty_category_map.csv`, with tests.
**S25 to S26. Staging views.** Rename and cast for all three sources. Two sessions.
**S27. Luhn macro.** `luhn_check()` plus a unit test with known valid and invalid NPIs.
**S28. Route resolution.** `longest_prefix_match()` macro and `int_drugs__route_resolved`.
Carry ADR-002 forward: naive equality drops roughly 450 Orenitram rows, and the test should
prove it still does not.
**S29. Remaining intermediates.** `int_claims__npi_validated`,
`int_providers__specialty_grouped`.
**S30. SCD2 snapshot.** `snap_provider` on specialty and location. The single best interview
artifact in the project, so get the `check` strategy and the grain right.
**S31. Dimensions.** `dim_provider`, `dim_drug`, `dim_date`.
**S32. Incremental fact.** `fct_claims`: merge strategy, `unique_key`,
`on_schema_change='append_new_columns'`, `{% if is_incremental() %}` lookback window.
**S33. Second fact.** `fct_patient_events`, incremental on event timestamp.
**S34. Aggregates.** `agg_specialty_route_share` and `mart_survival_cohort`.
**S35. Generic tests.** Built-ins plus `dbt_utils`: `expression_is_true`, `equal_rowcount` for
layer reconciliation.
**S36. Advanced tests.** `dbt_expectations` for distribution drift, plus three or four singular
tests. The best one asserts the CMS suppression rate sits inside an expected band, which encodes
domain knowledge no generic test can express.
**S37. Docs. GATE G3.** Description on every model and column, exposures for Streamlit,
Power BI and the survival notebook, `dbt docs generate`.
*The modeling resume bullet is now true.*

---

## Phase 3: Orchestration, serving, CI (S38 to S51)

**S38. Dagster scaffold.** `definitions.py`, resources for DuckDB and S3, `dagster dev` runs.
**S39. Extractor assets.** One `@asset` per source, wrapping the existing CLI logic.
**S40. dbt integration.** `@dbt_assets` with `DbtCliResource` so lineage is inherited rather
than hand-maintained.
**S41. Partitions.** Daily on patient events, yearly on CMS release.
**S42. Backfill.** Run a real backfill and screenshot the asset graph. The screenshot is a
deliverable, not a nicety.
**S43. Asset checks.** Freshness, row-count anomaly, null rate. This is the concrete answer to
the observability bullet in the posting.
**S44. Reliability.** `RetryPolicy(max_retries=3, delay=30, backoff=EXPONENTIAL)`, daily
schedule, sensor on CMS dataset version, failure hook writing to a pipeline alerts table.
**S45. Survival model.** Port the lifelines code as Dagster assets:
`survival_cohort` to `km_curves` to `cox_hazard_ratios`. Before writing it, confirm whether
lifelines is still absent from Snowflake's Anaconda channel; if it has landed, a Snowpark Python
model is the stronger artifact and this becomes ADR-013.
**S46. Write-back.** Results into marts, concordance index asserted near 0.647 as a regression
check against the Databricks build.
**S47. Streamlit one.** Specialty by route share view.
**S48. Streamlit two and three.** Stratified Kaplan-Meier, Cox tornado against HR = 1.0.
**S49. Deploy.** Streamlit Community Cloud, reading a committed DuckDB file. Public URL that
survives everything.
**S50. CI.** PR workflow: ruff, black, mypy, sqlfluff, then full `dbt build` on DuckDB with
`DBT_TARGET=duckdb`. Every test on every pull request, free. This is where the sqlfluff dbt
templater gets its first real exercise in CI.
**S51. Ship. GATE G4.** README, Mermaid architecture diagram, dbt docs to GitHub Pages.
*Public, clickable, runnable. Three of five resume bullets are true. Start applying.*

---

## Phase 4: Snowflake port (S52 to S63) - the trial clock runs here

Do not start this phase unless the next twelve sessions are reasonably protected. Twelve
sessions at five per week is seventeen days, inside the 30-day trial with margin. At three per
week it is four weeks and the trial expires mid-phase.

**S52. Account.** Register, choose Enterprise on AWS us-east-1. Immediately
`DROP WAREHOUSE COMPUTE_WH`. Resource monitor at 40 credits, 80 notify, 100 suspend, with the
notification email verified under Admin, Notifications. Nothing else this session.
**S53. Structure.** `01_warehouses.sql`, `03_databases.sql`. Three XS warehouses at
`AUTO_SUSPEND = 60`, databases, schemas, file format, `DATA_RETENTION_TIME_IN_DAYS = 7`.
**S54. Access.** `04_roles.sql`, `05_grants.sql`. Functional roles, role hierarchy into SYSADMIN,
and **future grants on schemas**, which is the clause people forget and which surfaces as
unreadable models two sessions later.
**S55. Service user.** `06_service_user.sql` with `TYPE = SERVICE`, RSA key pair generated
outside the repo, public key registered, private key into the Secrets Manager container from S5.
Confirm `RSA_PUBLIC_KEY_FP` is populated.
**S56. Storage integration.** The circular handshake: create the integration naming the S4 role
ARN, `DESC INTEGRATION` to read `STORAGE_AWS_IAM_USER_ARN` and `STORAGE_AWS_EXTERNAL_ID`, put
both into `terraform.tfvars`, re-apply, then `LIST @stage` to verify. Budget the whole session;
this is the fiddliest step in the project and the most valuable to have actually done.
**S57. Loader.** External stage, `COPY INTO`, `MERGE` on natural key. Repeated-run
reconciliation against Snowflake, same test as S13.
**S58. dbt on Snowflake.** `dbt debug` then `dbt build` on the snowflake target.
**S59 to S60. Dialect divergence.** Two sessions reserved, because the spec named this as the
most likely overrun and it is the main reason to have built DuckDB-first. Dispatched macros
where the adapters diverge. If it takes one session, bank the other.
**S61. Slim CI.** `CREATE DATABASE PAH_CI CLONE PAH_ANALYTICS`, then
`dbt build --select state:modified+ --defer --state ./prod-manifest`, then drop the clone.
Zero-copy cloning is Snowflake-specific, solves a real problem, and almost no portfolio has it.
**S62. Cost and recovery.** Monitoring model over `QUERY_HISTORY` and
`WAREHOUSE_METERING_HISTORY`: credits by warehouse, slowest models. Then a Time Travel recovery
drill, documented.
**S63. Power BI. GATE G5.** Repoint the existing semantic model at Snowflake via the native
connector in Import mode. Screenshots only. If sessions are scarce, this is the first thing to
cut; the Databricks screenshots already carry the enterprise-credibility story.

---

## Phase 5: Comparison and close (S64 to S70)

**S64. Airflow twin.** Astronomer CLI scaffold, one TaskFlow DAG, `astronomer-cosmos` rendering
dbt as a per-model task group. Screenshots. Hard timebox: one session, not two.
**S65. ADR-008.** Dagster versus Airflow for this workload, answered from having built both.
Asset-centric lineage and built-in quality checks against task-centric scheduling and market
ubiquity.
**S66. Elementary.** Install the package, generate the report, commit it.
**S67. ADR log.** Assemble ADR-001 through ADR-013, carrying the Databricks war stories forward.
**S68 to S69. Migration write-up.** The differentiated deliverable. Azure to AWS as well as
Databricks to Snowflake: storage model (Delta on ADLS to Parquet on S3), governance (Unity
Catalog to IAM plus Snowflake roles), ingestion (Auto Loader to external stages), orchestration
(Workflows to Dagster assets). Include the green-pipeline failures from ADR-010, which pair: the
Spark `.drop("Tot_Benes")` case-insensitivity bug, and the pre-commit hooks that passed while
checking nothing because the git root sat one level too high.
A quality gate that cannot fail is indistinguishable from one that passed.
**S70. Close. GATE G6.** Five resume bullets, now all true. LinkedIn post. Final README pass.

---

## Cut list, if the pace is lower than hoped

Apply in this order. Each saves sessions without falsifying a resume bullet.

| Cut | Saves | Cost |
|---|---|---|
| Power BI repoint (S63) | 1 | None. Databricks screenshots already cover it. |
| Tests from 60 to 40 (S35, S36) | 2 | Keep every singular test; drop redundant generics. |
| Streamlit from three views to two (S48) | 1 | Drop the Cox tornado, keep KM. |
| Airflow twin (S64, S65) | 2 | Loses a named CV tool. Cut only under real pressure. |
| ECS and ECR deployment | 0 | Already excluded. Do not reintroduce. |

Do not cut: the idempotency test, the SCD2 snapshot, the storage integration, slim CI on a
clone, or the migration write-up. Those five are the whole differentiator.

## Explicitly out of scope

Redshift or Athena as the warehouse (destroys the migration narrative). MWAA (roughly $350 per
month, destroys the cost bullet). Moving Streamlit off Community Cloud (loses the permanent
public URL). Glue or Athena as a third dbt target (DuckDB already fills that role). dbt Core 2.0
(release candidate; note it in the write-up, do not build on it).

## ADR register

| ID | Subject | Status |
|---|---|---|
| 001 to 007 | Carried forward from the Databricks build | Written |
| 008 | Dagster versus Airflow for this workload | S65 |
| 009 | Version pinning across two dbt adapters | Written |
| 010 | The silent quality gate | Written |
| 011 | AWS as substrate, not warehouse replacement | Written |
| 012 | DuckDB-first build order at two-hour session cadence | Written |
| 013 | Survival model placement: Dagster asset versus Snowpark | S45, conditional |
