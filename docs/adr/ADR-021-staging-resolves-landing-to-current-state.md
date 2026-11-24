# ADR-021: Staging resolves the append-only landing zone to current state

Status: Accepted, plan S26
Amends: the brief's "MERGE on natural key" ingestion rule, for CMS
Related: ADR-010 (prove a test can fail), ADR-019 (provenance contract v2),
ADR-020 (dbt reads the landing zone)
Amended by: ADR-023 (Phase 4 keeps every NPPES batch, not the NPPES MERGE below)

## Context

The landing zone is append-only. Every run writes a new batch prefix and
nothing is overwritten or deleted, which is what makes reruns safe (S13)
and history replayable. Under ADR-020 dbt reads the landing zone directly,
so something has to turn every version ever landed into the current
version. On Snowflake (Phase 4) the brief loads raw tables with MERGE on
natural key, so the raw layer there would already be current state.

## Decision

Staging resolves it, with one rule per source, chosen by what a new landing
means for that source:

| Source | A new landing is | Rule |
|---|---|---|
| CMS Part D | a full snapshot of one release year | the latest batch of each release year replaces that year whole |
| NPPES | a fresh lookup of some NPIs | latest successful lookup per NPI; a failure never displaces a hit or a miss |
| Patients | a possibly changed patient | latest updated_at per patient_id; extraction time breaks ties |
| Patient events | an immutable event | one row per event_id |

Each rule has a dbt unit test built on a crafted restatement. On today's
data (one batch per release, lookup or extract) no rule can fail, so
without the unit tests none of them would be proven (ADR-010).

## Consequences

- Staging means the same thing on both targets. On Snowflake, where raw
  tables arrive at current state, the rules become no-ops.
- CMS cannot be loaded into Snowflake with a row-level MERGE. A restatement
  that drops a row would leave the dropped row in the merged table, and
  Snowflake staging would disagree with DuckDB staging. Phase 4 loads CMS
  by replacing a release year: delete that year's rows and insert the new
  batch, in one transaction.
- The other MERGEs must carry their rule. NPPES updates a matched row only
  when the incoming lookup succeeded or the existing one failed. Patients
  update only when the incoming updated_at is not older. Events insert
  only when not matched.
- Postgres hard deletes are not captured: the source has no deleted flag,
  so a deleted patient keeps its last landed row. Acceptable for a
  synthetic source; a real application source would need CDC or a soft
  delete column.
- Landing keeps full history, so any rule can be changed and replayed
  without re-extracting. The NPPES history is also a possible input to the
  SCD2 snapshot at S30.

## Rejected alternatives

- **Latest row per natural key for every source.** Uniform and simple, and
  wrong twice: it keeps CMS rows a restatement removed, and it lets a
  timed-out NPPES lookup erase a good one.
- **Resolve at ingestion by overwriting or deleting in the landing zone.**
  Breaks the append-only guarantee that makes reruns idempotent and history
  replayable.
- **Resolve in the intermediate layer.** Staging is the first layer any
  model reads; leaving duplicates there means every downstream model must
  remember not to use it directly.
- **Defer to Phase 4.** The DuckDB target is permanent and must give correct
  answers now, not only after Snowflake arrives.
