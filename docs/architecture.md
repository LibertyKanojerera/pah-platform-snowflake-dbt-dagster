# Architecture

## Landing zone key layout

`<source>` is one of `cms_part_d`, `nppes`, `app_postgres_patients`, `app_postgres_patient_events`.

**`dt=` and `batch=` are Hive-style partition keys.** DuckDB `httpfs` and a Snowflake external
stage both understand this convention, so a query filtered on a date range prunes to the matching
prefixes instead of listing and reading every object in the bucket. The same layout therefore
serves both warehouse targets without translation, which is what makes the dual-target build
order in ADR-012 practical.

**`batch=<uuid>` is one extractor run.** A re-run writes a new prefix rather than overwriting an
existing one. This is what makes the idempotency test meaningful: two runs of the same extraction
produce two prefixes and the same reconciled row count after the merge, rather than one prefix
that silently replaced the other. It also means a bad batch can be identified and excluded
without touching the batches around it.

**`_manifest.json`** records row count, the source query or API parameters, and the extraction
timestamp. A landed batch can then be reconciled without re-reading the Parquet. The ingestion log
(`ingestion_log`, see `ingest/core/runlog.py`) is written by the extractor, one row per attempt, so
it also records failures and no-ops, which have no manifest.

## Provenance columns

Every landed row carries, in addition to the source fields:

| Column | Meaning |
|---|---|
| `_source` | Source id, matching `<source>` in the key. One per Postgres table (`app_postgres_patients`, `app_postgres_patient_events`), so every source has the same key depth and one glob reads them all. |
| `_batch_id` | The `batch=` UUID, so a row can be traced to its prefix |
| `_file_name` | Relative file path within the external stage (`METADATA$FILENAME`), not the full bucket key |
| `_ingested_at` | UTC timestamp of the extractor run |
| `_extracted_at` | When the value was read from its source. Equals `_ingested_at` for live reads; earlier only when served from a cache (NPPES). Contract: `_extracted_at <= _ingested_at`. Excluded from the row hash. ADR-019. |
| `_source_row_hash` | Hash of the source fields, for change detection |

These are set at landing, never in dbt, so the staging layer can be rebuilt from raw without
losing lineage.

## Buckets

| Bucket | Purpose | Retention |
|---|---|---|
| `pah-platform-tfstate-<account>` | Terraform remote state, native S3 locking (`use_lockfile`) | Versioned, never expired, `prevent_destroy` |
| `pah-platform-landing-<account>` | Raw landed objects | 90 days current, 30 days noncurrent, incomplete multipart uploads aborted at 7 days |

Both buckets have versioning, SSE-S3 encryption at rest and all four public access blocks set.

KMS was considered and rejected for this project: at this data volume the per-request KMS charges
would exceed the storage cost, and SSE-S3 already encrypts at rest. Revisit if the data ever
carries anything that is not public.

The `noncurrent_version_expiration` rule on the landing bucket is not optional. With versioning
enabled and only a current-version expiration rule, every overwritten object is retained
indefinitely as a noncurrent version: the bucket appears to expire data while the bill says
otherwise.

## Terraform layout

`infra/terraform/` holds plain resources rather than modules. With two buckets that differ in
exactly the ways that matter (`prevent_destroy` against lifecycle rules, state against data), a
shared module needs a toggle variable for every difference and reads worse than the duplication
it removes. Extract a module if a third bucket appears.
