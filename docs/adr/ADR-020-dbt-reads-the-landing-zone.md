# ADR-020: dbt reads the landing zone in place

## Status

Accepted 2026-11-17 (plan S23).

## Context

Phase 1 lands Parquet in S3 under `landing/<source>/dt=YYYY-MM-DD/batch=<uuid>/`. Each batch
writes its `_manifest.json` last, as a commit marker, and every row carries the provenance
contract from ADR-019. Phase 2 needs dbt to read that data, which raised three questions:

1. How DuckDB, running inside dbt, gets AWS credentials. The ingest code resolves the SSO login
   through boto3, because DuckDB's default credential chain could not do it at plan S19.
   dbt-duckdb sets up S3 access through DuckDB secrets in the profile. Its README documents the
   `credential_chain` provider; it does not document `use_credential_provider`, even though the
   adapter has that field.
2. Where to enforce the commit-marker rule. Since plan S8, the landing writer has promised that
   downstream readers skip any prefix without a manifest. dbt is the first downstream reader.
3. What freshness means for each source.

## Decision

**1. Credentials: DuckDB's own credential chain, set to `env;sso`.**

The profile defines one S3 secret:

- provider `credential_chain`, with the chain `env;sso`
- the profile taken from `AWS_PROFILE` (default `pah-admin`)
- region `us-east-1`
- validation `none`

Each part of the chain covers a different environment. The environment provider serves CI,
where GitHub OIDC sets environment variables, and later Dagster. The SSO provider serves local
development straight from the SSO login, so nothing is ever exported into a shell.

Tested at S23: naming the SSO provider explicitly read the bucket, where S19's default chain
had failed.

`validation: none` means parsing and linting work without an AWS session. A query that touches
S3 without credentials fails when it runs, not when the connection opens.

**2. One source, `landing`, with the commit marker enforced at the boundary.**

All four tables share one source-level `external_location` template, and dbt-duckdb fills
`{name}` with each table's name. The template reads only Parquet parts whose `batch=` directory
has a manifest. No model, test or freshness check can see an uncommitted batch.

The filter matches the path, not the rows' `_batch_id`, because the rule is about prefixes. A
part copied into an uncommitted prefix still carries its original, committed `_batch_id`, so a
row-based filter would let it through.

Proven at S23: a committed NPPES part copied into a prefix with no manifest doubled the raw
count to 4,478. The count through the source stayed at 2,239.

**3. Freshness on `_extracted_at` (ADR-019), with thresholds set by each source's cadence.**

| Table | Warn after | Error after | Why |
|---|---|---|---|
| `nppes` | 31 days | 45 days | The cache keeps entries for 30 days, so an observation can be that old |
| `app_postgres_*` | 7 days | 30 days | The synthetic app has no scheduled writer yet; tighten in Phase 3 |
| `cms_part_d` | none | none | CMS publishes annually; detecting a new release is the sensor's job |

The dbt-duckdb README says nothing about freshness for external sources. It was tested at S23
instead: all three checked tables passed.

## Rejected alternatives

**A wrapper script that exports credentials for one process.** It works, and it avoids the
stale-credentials trap, but it is a second mechanism to remember on every dbt command. It was
kept as the fallback in case DuckDB's SSO provider had failed.

**`use_credential_provider`.** The adapter has the field, but its README does not document it,
so nothing should depend on it.

**A row-based commit filter** (on `_batch_id`). It would let through the copied-part case
described in decision 2.

**One source per landing table.** That would mean four copies of the commit-marker rule, which
could drift apart.

**Enforcing the rule in staging models.** Freshness checks and ad-hoc `dbt show` queries would
still see uncommitted batches.

## Consequences

- **The bucket must be exported.** The source YAML reads `LANDING_BUCKET` through `env_var`,
  with no default. dbt does not read `.env`, so it is exported alongside `DBT_PROJECT_DIR` and
  `DBT_PROFILES_DIR`. Parsing fails loudly without it, by design.
- **Keep braces out of the template.** dbt-duckdb formats the `external_location` string with
  `str.format`, so it must contain no curly braces apart from `{name}`.
- **Every source query rescans S3.** That is fine at roughly 95,000 rows. Revisit if volumes
  grow.
- **Phase 4 must carry the rule over.** Snowflake reads the landing zone through a storage
  integration, and the commit-marker rule has to be implemented again there, for example by
  loading only batches listed in a manifest.
- **The ingest code could share this mechanism.** `ingest/core/duck.py` still resolves
  credentials through boto3. It could switch to the same `env;sso` secret, giving one mechanism
  for both. This is a candidate change, not yet made.
