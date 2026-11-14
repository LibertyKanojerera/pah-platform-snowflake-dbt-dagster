# Session Log

One entry per session. The `Next:` line is written before closing the laptop,
never at the start of the following session.

Entry `Sn` records plan item `S(n-1)`; its `Next:` line names plan item `Sn`.
The log is one ahead of docs/plan.md. Go by the plan item named, not the
entry number.

## S1 - 2026-10-25
Did: repo root confirmed correct; .env untracked; empty .gitignore found and
     populated (4th instance of the silent-gate pattern, ADR-010); AWS account
     created via "Sign up for AWS (new)" Builder ID flow rather than a classic
     account. Builder ID MFA set, project spend limit $20, $120 credits.
Blocked: no root user, so no root MFA and no IAM-access-to-billing toggle.
     AWS-managed SCPs on the project account are invisible to me.
Decided: sign up again via "Sign up for AWS (advanced)" for a classic root plus
     IAM account. Accept losing the $120; real spend is a few dollars a month.
Next: create the classic AWS account (alternate email or +aws alias), root MFA,
      enable IAM access to billing, $5 budget with 80% actual and 100% forecast
      alerts, region us-east-1. Then install Terraform and the AWS CLI.


## S2 - 2026-10-26
Did: classic AWS account created (036999274035, libertkanojerera@gmail.com),
     root MFA, IAM access to billing activated, $5 budget with 80% actual and
     100% forecast alerts, region us-east-1. IAM Identity Center enabled, user
     liberty with AdministratorAccess permission set, 4h sessions. AWS CLI v2
     and Terraform installed. aws sts get-caller-identity returns an assumed
     SSO role, no long-lived keys on disk.
Deferred: aws configure agent-toolkit (MCP server and skills), revisit around
     S6/S7 once the IAM and Terraform work is understood first-hand.
     Old Builder ID project at settings.aws.com still open, close it.
Next: terraform skeleton in infra/terraform/ with S3 remote state using native
      S3 locking (use_lockfile = true). Bootstrap with local state, then migrate.
      Write ADR-011 (AWS as substrate) and ADR-012 (DuckDB-first build order).

## S4 - 2026-10-27
Did: ADR-009 through ADR-012 written and committed. Terraform landing bucket
     applied: versioning, SSE, public access block, 90-day expiry plus 30-day
     noncurrent expiry, multipart abort at 7 days. Round-trip write/list/delete
     verified from the pah-admin role. Key layout documented in architecture.md.
Decided: plain resources rather than a bucket module. Two buckets differ enough
     (prevent_destroy vs lifecycle) that a module needs toggles for every
     difference. Extract if a third appears.
Next: plan S4, the IAM role Snowflake will assume. Scope the policy to the
      landing/ prefix only, leave the trust policy principal as a placeholder
      until DESC INTEGRATION in plan S56, and comment the circular dependency.

## S5 - 2026-10-30
Did: IAM role pah-platform-snowflake-storage-integration applied. Inline policy
     read-scoped to landing/ with an s3:prefix condition on ListBucket. Trust
     policy currently account root; snowflake_iam_user_arn and
     snowflake_external_id are null variables, so plan S56 is a tfvars change
     and an apply rather than a code edit. Verified trust policy has no
     sts:ExternalId condition yet, which is the expected pre-S56 state.
     ADR register rows 009, 010, 011 updated to Written.
Next: plan S5, Secrets Manager. One secret for the Snowflake key pair
      (populated at plan S55), one for Postgres. Refactor config loading so
      secrets resolve from Secrets Manager when AWS_PROFILE is set and from
      .env otherwise.

## S6 - 2026-10-30
Did: Secrets Manager containers for snowflake/keypair and postgres/app,
     7-day recovery window. Values deliberately out of Terraform, since
     aws_secretsmanager_secret_version writes plaintext into state.
     ingest/core/config.py resolves from Secrets Manager when AWS_PROFILE is
     set, .env otherwise; both branches verified to differ. First Python in
     the repo, so ruff/black/mypy finally ran and Passed rather than Skipped.
     docs/plan.md committed (it had never been saved to the repo; the README
     link was broken).
Fixed: use_secrets_manager used `is not None`, so AWS_PROFILE="" took the AWS
     branch. Now bool(). Empty-but-set env vars are common in CI.
Next: plan S6, GitHub Actions OIDC. Identity provider plus a role trusting
      this repo only, scoped to main and pull requests. Smoke workflow that
      assumes the role and lists the landing bucket. Closes gate G1.

## S7 - 2026-10-30
Did: OIDC provider and pah-platform-github-actions role. Trust policy scoped
     to main and pull_request on this repo only. GATE G1 CLOSED, aws-smoke
     green at f899677. PHASE 0 COMPLETE.
Debugged: sts:AssumeRoleWithWebIdentity denied for 4 runs. Cause was GitHub's
     immutable subject claims (April 2026): sub is now
     repo:<owner>@<ownerID>/<repo>@<repoID>:<context>, not the plain-name form
     every tutorial shows. Found by dumping the actual token claims in the
     workflow; no amount of inspecting the AWS side would have revealed it.
     Pinned the numeric IDs rather than wildcarding them. See ADR-014.
     Second failure was my own check: `aws s3 ls` exits 1 on an empty listing,
     so it could not distinguish "no objects" from "no access". Replaced with
     s3api list-objects-v2. ADR-010 again, this time in the verification.
Next: Phase 1, plan S7. ingest/core/http.py: requests session with tenacity
      retry, exponential backoff with jitter, structlog config, typed response
      wrapper. First real Python module.

## S8 - 2026-11-01
Did: ingest/core/log.py (structlog, JSON off-TTY, console on) and
     ingest/core/http.py: Session reuse, explicit (connect, read) timeout,
     retry on transient status only, exponential backoff with jitter,
     Retry-After honoured over computed backoff, frozen Response dataclass so
     sources are not coupled to requests. Verified 404 raises without retrying
     and 503 retries then raises.
Deviation: structlog config lives in ingest/core/log.py, not http.py. Logging
     is not HTTP-specific; the CLI and Dagster assets need it too.
Next: plan S8, ingest/core/landing.py. Parquet writer, batch UUID, partitioned
      key builder (landing/<source>/dt=/batch=/), manifest sidecar, S3 upload.

## S9 - 2026-11-01
Did: ingest/core/landing.py. Batch (uuid per run, Hive-style
     landing/<source>/dt=/batch=/ prefix), row_hash (canonical sorted JSON,
     provenance columns excluded), add_provenance, zstd Parquet parts via
     pyarrow, _manifest.json written last as a commit marker so a prefix
     without one is an incomplete batch. Round trip verified: wrote to S3,
     read back through DuckDB httpfs with provenance intact. Hash stability
     verified against key reordering.
Next: plan S9, ingest/core/state.py. Checkpoint read/write against a local
      DuckDB table now, META.INGESTION_STATE later. Written only after a
      successful load.

## S10 - 2026-11-01
Did: ingest/core/state.py. StateStore ABC with a checkpoint() context manager
     whose write sits after the yield, so a raising body has no write path at
     all. DuckDBStateStore with ingestion_state plus an append-only
     ingestion_state_history. Regression guard, overridable for a deliberate
     full refresh. Verified the failure path explicitly: advance() followed by
     an exception leaves the previous watermark intact.
     This is the corrected form of the Auto Loader stale-checkpoint bug from
     the Databricks build. Candidate for ADR-015 at S67.
Next: plan S10, CMS reconnaissance. Hit data.cms.gov by hand, document the
      response envelope, pagination parameters and filter syntax in
      docs/sources.md, save a sample to data/samples/. No extractor code.

## S11 - 2026-11-01
Did: plan S10, CMS reconnaissance. docs/sources.md rewritten with proper
     tables. No extractor code, by design.

Findings:
- One dataset UUID per release, no version parameter. 13 entries, 2013 to
  2024. Resolved at runtime from https://data.cms.gov/data.json rather than
  hardcoded; the same query is what the S44 sensor will watch.
- Envelope is a bare JSON array. No wrapper, no count, no next link.
- Every value is a string, including numerics and FIPS codes. Land as string,
  cast in staging at S25 with explicit empty-string handling.
- Suppression has three states and none is null: "", "#", "*". A row can
  carry claims and costs while its beneficiary count is withheld. "" means
  "not suppressed" in a flag column and "suppressed" in a count column, so an
  IS NULL check in staging would classify every row as unsuppressed.
- Page size ceiling is 6500, measured: 5000 -> 5000, 6500 -> 6500,
  6600 -> 6500, 8000 -> 6500. Row cap, not byte budget.
- 28,023,892 rows in the latest release. Server-side filtering mandatory.
- filter[Column]=Value CONFIRMED, exact match. Verified by contrast rather
  than assumption: unfiltered head is Abrysvo and Boostrix Tdap, filtered head
  is Orenitram Er. /data/stats respects the filter too.
- keyword= works as a broader fallback. column= does NOT accumulate across
  repeated parameters; syntax unresolved.

Gotcha: the first two filter probes returned nothing and I read it as an API
     rejection. It was curl treating [ and ] as a glob range: curl: (3) bad
     range in URL. Needs -g at the shell. No issue from Python, where
     requests URL-encodes the brackets.

Biggest find: the stored values are not the names anyone would guess. Brand is
     "Orenitram Er", not "Orenitram". Generic is "Treprostinil Diolamine", not
     "Treprostinil". Exact equality on either guess returns zero rows and the
     pipeline runs green reporting no PAH prescribing in the US. Same shape as
     ADR-002 on the Databricks build.

Wrote: ADR-015 (filter on Gnrc_Name against a version-controlled vocabulary,
     derive route from Brnd_Name, guard with an unknown-value keyword sweep).
     Route is a brand property, not a molecule property: treprostinil ships as
     Remodulin infusion, Tyvaso inhaled and Orenitram oral, and route is the
     dependent variable of research question one.
     ADR-014 expanded from a stub into the full immutable-claims story.

Still open, carried into S11: two UUIDs share the title date 2024-12-01 and it
     is unclear whether that date is the data period or the release date, so
     the year-to-UUID mapping is not yet trustworthy; result-set ordering
     stability without an explicit sort is unverified, and if unstable then
     offset pagination can skip or duplicate rows and the S13 idempotency test
     will fail intermittently; column= syntax; rate limits unobserved.

Next: plan S11, CMS pagination. ingest/sources/cms_part_d.py: offset and size
      loop with stride 6500, total from /data/stats with the filter applied,
      termination conditions. Resolve the ordering-stability question first,
      before writing the loop.

## S13 - 2026-11-02
Did: plan S12. DrugVocabulary (reads transform/seeds/drug_vocabulary.csv,
     shared with dbt), sweep_for_unknown_drugs (keyword sweep per molecule
     stem, raises on any pair absent from the vocabulary), _release_year from
     temporal rather than the title, land_release (filter per generic, tag
     _cms_release_year and _cms_dataset_uuid, one batch per release, manifest
     carries rows_per_generic). Landed 2023: 1,690 rows, verified by DuckDB
     read-back; pair counts reconcile against the generic-level counts.

Vocabulary established by sweeping all 13 releases: 12 pairs, 9 distinct
     generic names. Four patterns that would each break a naive build:
     a generic name moves between releases (Tyvaso filed under
     Treprostinil/Neb Accessories in 2013, Tyvaso Dpi under Treprostinil in
     2023); one brand carries several generic names (Veletri under both
     Epoprostenol Sodium and Epoprostenol Sodium (Arginine), the diluent
     inconsistently recorded); brand sometimes equals generic (authorized
     generics of Remodulin and epoprostenol); and Tyvaso Refill Kit is
     nebulizer supplies, not a drug, so a product_type column was added keyed
     off brand rather than generic.

Gotcha: aws configure export-credentials leaves AWS_ACCESS_KEY_ID and friends
     in the shell, and those take precedence over AWS_PROFILE in boto3. Once
     expired, writes fail with "refreshed credentials are still expired" while
     the SSO session is healthy. Unset all three, or export in a subshell.
     Recorded in sources.md.

For S25: exclude product_type='accessory' from claim counts. 4 of 1,690 rows
     in 2023, small but they are not prescribing events.
For S36: molecules need per-molecule validity windows, not a blanket
     all-four-every-year assertion. Selexipag absent before 2016; iloprost
     present 2013, absent 2023.

Next: plan S13, the idempotency test. pytest: run the 2023 extraction twice,
      assert identical row counts and no duplicate natural keys. Grain is
      (Prscrbr_NPI, Brnd_Name, _cms_release_year) per the S11 finding; confirm
      it holds across brands before asserting on it.

Byte-identical output: four separate runs produced part-0000.parquet at
     exactly 130,853 bytes and _manifest.json at 1,400 bytes every time. Row
     order, Parquet encoding and zstd compression are all reproducible, not
     just the row content. Only the batch UUID differs.

## S14 - 2026-11-03
Did: plan S13. Two-tier test suite. 17 unit tests with hand-rolled HTTP and
     S3 stubs (no moto, no network, run in CI); 5 integration tests marked and
     excluded by default, hitting the live API and real S3.
     Unit coverage: row hash stability across key order and across provenance,
     manifest written after data, batch prefixes never collide, write_part
     refuses empty input, iter_rows raises on both truncation and
     over-delivery, resolve_dataset collapses identical duplicates but refuses
     differing temporal, sweep raises on unknown vocabulary pairs.
     Integration: both runs return the same row count and the same row-hash
     set; natural key unique within a batch; no brand under two generics in
     one release; union across both batch prefixes deduplicates to one batch.

Settled: the natural key is (Prscrbr_NPI, Brnd_Name, _cms_release_year),
     verified across all eight pairs rather than the single brand measured at
     S11. Gnrc_Name is NOT needed. This is fct_claims' unique_key at S32.

Proved the suite can fail: broke row_hash to include provenance, confirmed
     test_row_hash_ignores_provenance_columns failed with a hash diff, then
     reverted. ADR-010 discipline — a suite that has only ever passed has not
     been shown capable of failing.

Fixed: DEFAULT_VOCABULARY_PATH was CWD-relative, so it resolved only from the
     repo root and errored under pytest from tests/. Now anchored with
     Path(__file__).resolve().parents[2]. Would have broken Dagster at S39 and
     CI at S50, both of which run from elsewhere. Same family as the
     September git-root bug: works from one directory, fails from another.

Next: plan S14, NPPES reconnaissance. Response shape, 404 behaviour, observed
      rate limits, sample into data/samples/. No extractor code.

## S15 - 2026-11-04
Did: plan S14, NPPES reconnaissance. docs/sources.md gains an NPPES section;
     data/samples/nppes_sample.json holds a found and a not-found response.

Findings:
- version param is mandatory; omitting it or passing 9.9 both return
  {"Errors": [{"description": "Unsupported Version", ...}]} rather than
  falling back. Errors arrive in the body, so http.py cannot be relied on to
  raise.
- Envelope is {"result_count": N, "results": [...]}. One record per NPI, no
  bulk endpoint, so enrichment is one request per distinct NPI.
- Not found is HTTP 200 with result_count 0, NOT a 404. The dead-letter path
  at S16 therefore lives in nppes.py keyed on result_count, not in the HTTP
  layer. ADR-010 family: at the transport layer a miss and a hit are the same.
- taxonomies is an array with a `primary` boolean. Observed record has one
  entry. Unresolved: what to do when none is primary or several are. Picking
  [0] would silently misclassify specialty, the independent variable of
  research question one. Must settle before S29.
- addresses is an array with address_purpose LOCATION | MAILING. Use LOCATION.
  postal_code is unformatted ZIP+4 with no hyphen; truncate to 5 for joins.
- Rate: 30 sequential requests in 3.50s, all 200, no throttling. ~8.6 req/s.
  Concurrent ceiling deliberately not probed.
- Workload: 1,100 distinct NPIs across 11,830 rows, so a full pass is ~2 min.
- All 1,100 NPIs are individuals. No NPI-2 organisations in the PAH
  prescriber set, so that branch will be untested by this data.
- No history in the API. basic.last_updated says when a record changed, not
  what it was. Observed record last changed 2020-12-04.

Decided: SCD2 at S30 snapshots CMS-derived attributes, not NPPES. Prscrbr_Type
     varies across 11 release years in data already landed, giving genuine
     deltas, and it is the specialty field the research question uses. NPPES
     stays a point-in-time enrichment. Mechanism: run the snapshot once per
     release year in sequence to build the history.

Next: plan S15, ingest/sources/nppes.py. Batched NPI lookup with a
      token-bucket throttle at or below 8.6 req/s, result_count branch for
      misses, handle NPI-1 and NPI-2.

## S16 - 2026-11-06
Did: plan S15. ingest/core/throttle.py (TokenBucket, monotonic clock, burst to
     capacity then settle to rate) and ingest/sources/nppes.py: one request
     per NPI, result_count branch for misses, Errors-in-body check, flatten to
     a row with _raw_response retained as canonical JSON. 19 new unit tests.
     Full run: 1,100 NPIs at 5 req/s in ~4 minutes, 1,095 found, landed to
     landing/nppes/dt=2026-11-06/batch=a3e7ecfb.
     Join verified: 1,100 CMS NPIs, 1,100 matched, 1,095 enriched.

Design calls:
- Flattened at landing with _raw_response kept. The two judgment calls
  (which taxonomy is the specialty, which address is the practice location)
  belong in tested code, not re-expressed in SQL per model. Nothing is lost.
- Taxonomy resolution: one primary -> take it; single taxonomy with no
  primary -> take it; otherwise pick the lowest code deterministically and
  set taxonomy_ambiguous. Deterministic so re-runs agree and the idempotency
  guarantee holds. Never a silent pick.
- Throttled at 5 req/s against a measured ceiling of ~8.6. Free public
  service, 1,100 requests, the difference is two minutes.

Measured:
- 60% of providers carry several taxonomies (30 of 50 sampled), but
  ambiguous_taxonomy is 0 across all 1,100. The primary flag is reliable in
  this dataset without exception, so the fallback is defensive code covered
  only by unit tests. Do not claim it handles a case the data contains.
- 5 of 1,100 NPIs are absent from NPPES: 1255345963, 1275615494, 1487613089,
  1598772931, 1659374593. All prescribed in the 2023 release. Absent from the
  registry as of today, three years later. Deactivation is likely but cannot
  be distinguished from "never registered" with one release year landed.
  Open until the backfill at S19.

Fixed: _primary_taxonomy's ambiguous branch returned the sorted list while
     every other branch returned a dict, violating its own signature. mypy
     would have caught it once staged; it was masked by an isinstance guard
     in flatten. Both removed, regression test added.

Corrected an assumption: the landing zone holds the 2023 release SEVEN times
     (S12 landing plus six integration-test runs). 7 x 1,690 = 11,830, which
     is the row count we had been reading as eleven years of data. Only 2023
     is landed; twelve release years remain. The 1,100 NPIs and the specialty
     distribution are single-year figures.
     Consequence for S23: _sources.yml cannot glob the landing zone naively.
     Staging must deduplicate on (Prscrbr_NPI, Brnd_Name, _cms_release_year)
     or select the latest batch per release year. The landing zone is
     deliberately append-only; the staging layer owns deduplication.

Stray: a copy of test_both_runs_produce_identical_source_row_hashes had been
     duplicated into tests/test_landing.py, unmarked, where it ran in the
     fast

## S17 - 2026-11-08
Did: plan S16. ingest/core/cache.py (JsonCache on DuckDB, namespaced, TTL
     30 days) and NPPES resilience in iter_lookups.

Three outcomes, not two, each handled differently:
  found   -> cached, nppes_found=True
  miss    -> cached (negative), nppes_found=False, nppes_error=''
  failure -> NOT cached, nppes_found=False, nppes_error set, run continues
Caching misses stops the 5 unfound NPIs being re-queried every run. Not
caching failures means a transient error retries next run instead of becoming
a hole that lasts the TTL.

Failure tolerance: a per-NPI exception is caught, recorded and yielded, so one
     bad lookup no longer discards the other 1,099. Bounded by a 5% failure
     ceiling checked after 50 attempts, so a batch where most lookups errored
     aborts rather than landing with a valid manifest and reading as a quiet
     period. ADR-010.

Measured: 100 NPIs, run 1 19.1s, run 2 1.0s, 100 cache hits, zero network.
     The remaining second is 10ms per cache read from opening a DuckDB
     connection per get - deliberate, same single-writer reason as the
     checkpoint store. At 1,100 NPIs that is ~11s of overhead. A get_many()
     doing one WHERE key IN (...) would collapse it if the NPI count grew an
     order of magnitude. Known cost, named remedy.

Deviation: no dead-letter table. It would duplicate state already in the
     landed rows, where nppes_error <> '' identifies every failure, queryable
     by dbt with batch and timestamp attached. And because failures are not
     cached, the retry a dead-letter table usually drives already happens
     automatically on the next run. The dead letter is a query, not a table.

Also: separate cache.duckdb from the checkpoint store. The cache is
     disposable, the checkpoint is not; sharing a file would mean you cannot
     throw away one without risking the other.

Fixed: manifest had `failed` and `errored` as the same expression. Replaced
     with found / missed / failed, which are three distinct outcomes, so
     requested == found + missed + failed is an invariant S36 can assert.
     _primary_taxonomy now uses min() rather than sorted()[0] - same
     semantics and determinism, O(n) not O(n log n). Ruff flagged it.

Next: plan S17, synthetic patient events. Finish infra/postgres/seed.sql and
      the generator: 5,000 patients, event timestamps, copay tier, initial
      route, prescriber NPI drawn from the CMS set so the join resolves.

## S18 - 2026-11-09
Did: plan S17. infra/postgres/docker-compose.yml, seed.sql, generate.py.
     Loaded 5,000 patients and 77,192 events into Postgres.

Design: the hazard is designed, not incidental. Each patient's time to
     discontinuation is drawn from an exponential whose rate is modulated by a
     proportional-hazards term built from TRUE_COEFFICIENTS, declared in
     generate.py and version-controlled. Route, copay tier, age band and
     prescriber specialty all participate. Prescriber NPIs come from the
     landed CMS set and specialty from the landed NPPES enrichment, so the
     synthetic cohort joins to real data and specialty genuinely affects the
     outcome rather than being decorative.

     That makes the ground truth knowable. S46 asserts the fitted Cox model
     recovers each coefficient within its confidence interval, which is a far
     stronger test than a concordance index: a C-index can look respectable on
     a model that has learned nothing in particular, whereas recovering a
     known log hazard ratio cannot happen by accident.

Correction to plan S46: "concordance index asserted near 0.647 as a regression
     check against the Databricks build" is not achievable. That number came
     from a different synthetic cohort generated by a different script, so
     asserting on it would be asserting on a coincidence. The regression check
     is against TRUE_COEFFICIENTS, plus whatever C-index this seeded cohort
     produces, pinned once measured.

Watermark: schema carries both event_timestamp (when the clinical event
     happened) and created_at (when the row arrived). Incremental extraction
     at S18 watermarks on created_at. A business timestamp as a watermark
     silently drops late-arriving records: a fill recorded Tuesday for a
     Friday dispense is already behind a watermark set from Friday's events.
     The plan item for S18 says event_timestamp; it is wrong. ADR-018 still
     to write.

Cohort: 1,100 prescribers (47% pulmonology), 5,000 patients, 3,727
     discontinued, 1,273 censored (25%), 77,192 events. Censoring came in
     below the 30-45% target band and was deliberately NOT tuned - 3,727
     events is ample power for seven coefficients and 25% still exercises the
     censoring machinery. Adjusting the generating process to hit a cosmetic
     target risks disturbing something that works.

Verified: copay gradient is monotonic in raw means - 16.1, 15.6, 13.2, 11.8
     against designed coefficients 0, 0.15, 0.35, 0.55. Crude hazard ratio for
     inhaled is 1.27 against a designed 1.28.

Investigated: parenteral's crude HR came out 0.909 against a designed 0.705,
     2.6 standard errors out. Re-running the generator at n=50,000 gave 0.748,
     converging as sampling error shrinks. The generator is correct.

     The residual ~1.8 SE gap has a known cause: the hazard ratio is
     non-collapsible. The crude events-per-person-month ratio estimates a
     MARGINAL hazard ratio averaged over the other covariates; TRUE_COEFFICIENTS
     are CONDITIONAL. The marginal estimate is biased toward 1, because the
     surviving population in the lower-hazard arm is less depleted of frail
     individuals and carries a higher average hazard than proportionality
     predicts. Observed direction matches: 0.705 conditional appears as 0.748
     marginal. Only the adjusted Cox fit at S46 estimates the designed
     quantity. Noted in the generator docstring so it does not read as a bug.

Lesson, and the one worth carrying: the first diagnostic I ran was mean
     observed follow-up by route, which showed parenteral as SECOND longest
     rather than first and looked like a broken generator. Mean follow-up is
     biased under administrative censoring and the bias is worst for the
     longest-surviving group - parenteral's true mean of ~37 months against
     ~33 months of available follow-up means the calendar, not the hazard,
     caps most of them. Acting on that diagnostic would have meant "fixing" a
     generator that was right. The metric was unsuitable, not the data.
     Same discipline as ADR-010, applied to statistics rather than tooling.

Gotchas:
- POSTGRES_PASSWORD is baked in when Postgres initialises an empty data
  directory. Changing .env and restarting does nothing; docker compose
  down -v is required. Costs an hour if you do not know it.
- Connection failed with "password authentication failed" because the dual-
  path config routed to Secrets Manager (AWS_PROFILE was set) where the
  placeholder "CHANGE_ME" from plan S5 had never been replaced. The config
  worked exactly as designed; the AWS branch had simply never held a real
  value. Both .env and the secret now carry the same generated password.
- infra/ was outside the pre-commit scope (^ingest/ only), so generate.py got
  no linting or type checking. Extended to ^(ingest|infra)/.*\.py$. Same class
  of gap as ADR-010: a control that cannot see the files it is meant to check.

Next: plan S18, ingest/sources/app_postgres.py. Incremental extraction on a
      created_at high-water mark using the checkpoint store from S9. Land to
      S3 as a third source. Write ADR-018 on the watermark choice while the
      reasoning is fresh.

## S19 - 2026-11-10
Did: plan S18. ingest/sources/app_postgres.py: incremental extraction of
     patients and patient_events, each with its own checkpoint. Server-side
     cursor with itersize 10,000 so the client never buffers the full result.
     Parts of 50,000 rows.

     First run: patients 5,000 (1 part), patient_events 77,192 (2 parts).
     The project's first multi-part batch - write_manifest has supported it
     since S8 and had never been exercised.
     Second run: no_new_rows and state.not_advanced for both. That pair is
     the incremental claim.

ADR-018 implemented and enforced:
- Watermark reads created_at, never event_timestamp.
- Upper bound is now() - interval '1 minute', evaluated by the DATABASE so
  the comparison uses one clock. A client-side bound adds host skew as a
  second source of the same bug.
- The watermark advances to max(created_at) of rows actually landed, not to
  the query bound. The gap between the two is where a late-committing
  transaction lands.
- No rows means no advance. An empty result is evidence of nothing.

Three integration tests, and the middle one is the point:
- a late-arriving row (event_timestamp 2019, created_at 5 min ago) IS
  extracted;
- an event_timestamp watermark WOULD have missed it - asserting the rejected
  alternative fails is what makes ADR-018 enforced rather than opinion;
- the safety lag holds back a row inserted moments ago.
On well-behaved data both watermark choices give identical results, so
without these the two implementations are indistinguishable.

Observed under a real failure: the first extractor run died inside write_part
     on expired credentials. The checkpoint did not advance (the write sits
     after the yield) and no manifest was written, so there was no prefix for
     downstream to mistake for a complete batch. Both guarantees held against
     an unplanned failure rather than a simulated one.

Watermark asymmetry worth knowing: patients advanced to 2026-11-09 (real
     wall-clock, DEFAULT now() fired because COPY omitted created_at) while
     patient_events advanced to 2024-01-01 (synthetic, the generator backdates
     created_at to event time + 1-2 days). Different ingestion-time semantics
     per table; the events table is the more realistic of the two.

Test hygiene: an integration test that mutates shared state must clean up on
     the FAILURE path, not just on success. A run that died before cleanup
     left event_id 999000002 behind and every later run then failed on the
     primary key rather than on the property under test - a misleading error
     that looks like a bug in the thing being tested. Cleanup now sits in a
     finally, and each test deletes its id before inserting.

Fixed permanently: a `withaws` shell function runs a command with exported
     credentials in a SUBSHELL, so they never leak into the parent. Third
     occurrence of "Credentials were refreshed, but the refreshed credentials
     are still expired" - env vars outliving their session and shadowing
     AWS_PROFILE for boto3.

Next: plan S19, ingest/cli.py. One entry point, three subcommands,
      --full-refresh and --dry-run. Then the multi-year CMS backfill, which
      settles the open question about the 5 NPIs absent from NPPES.

## S20 - 2026-11-11
Did: plan S19. ingest/cli.py with cms, nppes, postgres and all subcommands,
     --dry-run and --full-refresh throughout, installed as `pah-ingest`.
     land_all_releases checkpoints PER RELEASE, so a crawl that dies on
     release nine resumes at nine. Third use of the checkpoint store with
     different semantics again - the watermark is a release year.
     releases_by_year keys on temporal endDate, not the title, and collapses
     the duplicate 2024 registration.

Ran the full backfill. 12 release years (2013-2024; the 13th catalog entry
     was the 2024 duplicate), 2,239 distinct NPIs, 22,701 landed rows. That
     row count is inflated by ~8 copies of the 2023 release from earlier runs
     and tests; distinct source rows are roughly 10,900. Staging deduplicates
     at S23.
     NPPES re-run: 2,239 rows, 2,220 found, 19 missed, 0 failed, entirely
     from cache - 56 seconds against ~7.5 minutes of live requests.

Settled the question open since S15. 19 NPIs appear in CMS and return
     result_count 0 from NPPES. They are DEACTIVATED registrations, not
     errors. The decisive case is 1598772931, which billed Medicare in every
     release year from 2013 to 2024: CMS validated that NPI at claim time for
     eleven consecutive years, so it certainly existed. NPPES excludes
     deactivated NPIs entirely rather than returning them with an inactive
     status, which makes deactivation indistinguishable from non-existence at
     the API level. Five are 8-11 year prescribers, nine are 2-6 years, five
     are one year.

     The general point, and it is the one worth carrying: the dimension is
     not a superset of the fact table's keys and cannot be. Historical claims
     reference providers who have since left the registry. Retrospective
     justification for the left join with nullable enrichment (ADR-017) and
     for rejecting NPPES as the SCD2 source at S14 - snapshotting a registry
     that silently drops members would record deletions it cannot explain.
     S29's specialty grouping must tolerate a null taxonomy.

Added: ingest/core/duck.py. boto3 resolves the SSO profile and hands DuckDB
     the concrete session credentials, so reads of the landing zone no longer
     need credentials exported into the shell. Retires the `withaws` dance and
     the stale-environment failure that broke three runs. generate.py's
     load_prescribers should be retrofitted to use it.

Next: plan S20, observability. Structured logging to an ingestion log table:
      rows read and written, duration, batch id. Audit provenance columns
      across all three sources for consistency.

## S?? - 2026-11-13
Did: plan S20, blocks 2 to 4 of 5. ingest/core/runlog.py: ingestion_log in
     the state DuckDB file, one row per attempt (success, no_new_rows,
     failed). rows_read counted where rows arrive, rows_written from landed
     parts, monotonic duration, run_id injected and bound into structlog
     contextvars. checkpointed_attempt() is the only place the nesting is
     written: attempt outside, checkpoint inside, so success means landed
     AND checkpointed. 11 unit tests; three mutation checks each turned the
     right test red (nesting swapped, _finalise_status moved out of the try,
     bare raise deleted). Wired through cli.py and all three sources. A clean
     exit that cannot be recorded truthfully writes a failed RunLogError row
     before raising; CMS discovery failure and the NPPES NPI read also leave
     failed rows.

Fixed: S20 audit finding 3. _file_name held the bare part name, which every
     batch shares; now the full object key. Test writes through FakeS3 and
     reads the Parquet back; proven able to fail.

Live runs: cms (no_new_rows, checkpoint 2024), postgres --full-refresh
     (5,000 and 77,192 rows, 1 and 2 parts), postgres (two no_new_rows),
     nppes (2,239 rows, all from cache, 19 missed). Six rows, rows_read equal
     to rows_written on every success. Every stderr line carries run_id and
     attempt_id, including landing and state lines that know nothing about
     the run log.

Reconciliation: tonight's three success batches match their manifests.
     Only landed_not_logged remains, all pre-S20: cms 19, nppes 3, postgres
     1 each. CMS 19 is consistent with 11 releases landed once plus 8 copies
     of 2023 (S12, integration tests, the S19 backfill): finding 1, counted.
     Finding 4 is in the bucket: all 2,239 NPPES rows landed tonight came
     from cache and carry tonight's _ingested_at.

Decided (ADR-019, drafted, not yet committed): sixth provenance column
     _extracted_at; underscore naming rule (drop _source_table, rename
     _raw_response); UTC-normalised row hashing; test landings under
     landing_test/ with their own lifecycle rule. All implemented at S21.

Corrected: mypy does not cover tests/; ruff and pytest --collect-only are
     the checks there. Block 1, the contract test, slipped and opens the
     next session.

Next: finish plan S20. Block 1, the provenance contract test (first run is
      the audit; compare with the predicted red list). Block 5: grep
      architecture.md before editing, strict-xfail tz test, save ADR-019.
      Then S21: provenance v2, then G2 (G2 slips to S22 rather than
      compress).
