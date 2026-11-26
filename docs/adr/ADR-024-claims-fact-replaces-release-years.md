# ADR-024: The claims fact replaces whole release years, not rows

Status: Accepted, plan S32
Amends: the spec's fct_claims "merge strategy" and "lookback window"
Related: ADR-010 (prove a test can fail), ADR-018 (watermark on arrival,
not event time), ADR-021 (staging resolves landing), ADR-023 (provider
history)

## Context

The spec asks for fct_claims to be incremental, with a merge strategy, a
unique_key, on_schema_change='append_new_columns' and an is_incremental()
lookback window for late-arriving rows.

A CMS release is a full snapshot of its year, and staging takes the latest
landed batch of each year whole (ADR-021). A restatement can therefore
drop a row. A merge on the claim grain updates the rows it matches and
inserts the new ones, but it never deletes a row the source no longer has:
the dropped row stays in the fact, and the fact and staging disagree.
ADR-021 rejected a row-level MERGE for loading raw CMS data into Snowflake
for the same reason.

A lookback window works out what arrived recently from a timestamp, with a
margin for rows that arrive late. CMS data does not arrive row by row. It
arrives as one batch per release year, and staging already says which
batch is current for each year.

## Decision

- incremental_strategy='delete+insert' with unique_key='data_year'. A
  release year that loads is deleted from the fact and inserted again
  whole.
- A year loads when staging's batch for it is not in the fact: an
  anti-join on (data_year, _batch_id). A new year or a restated one loads;
  an unchanged year is not touched. There is no lookback window, because
  an exact comparison has nothing late to catch.
- on_schema_change='append_new_columns', as the spec asks.
- Each claim joins the dim_provider version in effect on data_year_end,
  the last day of its data year. A claim row covers a whole year, so any
  single date is a convention; the year end is the latest state within
  the year and the closest to the NPPES history, so it backfills least.
  is_provider_backfilled marks a claim dated before its version was first
  observed (ADR-023).
- _loaded_at is dbt's run_started_at, so the fact shows which years each
  run touched.
- The fact keeps CMS's prescriber type and state for the year (ADR-023).
  CMS's prescriber name, city and state FIPS stay in staging: the provider
  dimension, from NPPES, owns those.

## Evidence

On the real data at S32 (10,871 rows, 12 release years, 398,062 claims):

- A rerun with nothing new loaded nothing: the fact kept one _loaded_at.
- A faked restatement of 2019, its 981 rows marked as coming from a
  superseded batch and one row added that the release no longer has,
  failed assert_fct_claims_matches_staging with 982. The next incremental
  run replaced 2019 whole: the test passed, the extra row was gone, and
  only 2019 had a new _loaded_at.
- The same restatement under the spec's merge (unique_key on the grain)
  left the extra row in place, and the test failed with 1. Restoring
  delete+insert and running again did not remove it, because the merge
  had rewritten 2019's batch ids to the current ones and nothing looked
  new. Only --full-refresh did.
- Unit tests pin two rules that no data test can see broken. Joining as
  at the first day of the year changed two of four rows (2019 marked
  backfilled, 2020 joined the earlier version). Dropping the incremental
  filter let two unchanged years through; on the real data that would
  only reload every year on every run.

## Consequences

- After every run the fact matches staging row for row, and
  assert_fct_claims_matches_staging checks it on every build. A release
  year withdrawn from the landing zone stays in the fact until a full
  refresh, and the test reports it.
- A dimension change reaches years already loaded only on --full-refresh.
  That is safe today: every claim year through 2025 ends before the NPPES
  history starts, so it joins version 1 however many versions accumulate.
  Data year 2026 is the first that can join a later version.
- dbt-snowflake supports delete+insert, so the model runs unchanged in
  Phase 4.
- fct_patient_events (S33) decides its own strategy. Events are rows that
  arrive late, not snapshots, so the spec's merge and lookback window fit
  there, and ADR-018's point about watermarking on arrival rather than
  event time applies.

## Rejected alternatives

- **A merge on the claim grain (the spec).** It keeps a row a restatement
  dropped, and S32 showed that a later incremental run cannot repair it.
- **A lookback window on _ingested_at.** It reloads every year that landed
  inside the window for nothing, and it relies on ingestion clocks to say
  what is new, where comparing batch ids does not.
- **A table.** Correct and simpler: a full rebuild takes about three
  seconds and would stay cheap for years at about 2,000 rows a release.
  The incremental model is kept because the spec asks for one and because
  its load rule is the part worth deciding and testing, not because it
  saves time at this size.
