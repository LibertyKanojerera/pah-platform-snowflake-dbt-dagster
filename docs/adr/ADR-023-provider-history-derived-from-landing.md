# ADR-023: Provider history is derived from every NPPES batch, not captured by a dbt snapshot

Status: Accepted, plan S30
Supersedes: the plan S14 decision to build the SCD2 from CMS attributes
Amends: the spec's snap_provider snapshot and SNAPSHOTS schema; ADR-021's
NPPES MERGE consequence
Related: ADR-010 (prove a test can fail), ADR-019 (provenance contract v2),
ADR-020 (dbt reads the landing zone), ADR-021 (staging resolves landing)

## Context

The spec asks for snap_provider, a dbt snapshot keeping SCD2 history of
provider specialty and location, so that a claim joins to the provider as
at claim date. The NPPES API has no history: it returns each record as it
is now.

Plan S14 therefore decided to build the history from CMS instead. CMS
publishes prescriber type and state per release year, both already vary
across the years landed, and CMS's prescriber type was then the specialty
the research questions used. S20 added a second reason: NPPES leaves out
deactivated NPIs entirely (19 CMS prescribers today), so a snapshot of it
would record deletions it cannot explain.

Two things have changed since. S29 made the NUCC taxonomy code from NPPES
the specialty for both research questions (docs/sources.md, Specialty), so
CMS's prescriber type no longer feeds any analysis. And under ADR-020 the
landing zone keeps every NPPES lookup with the time it was fetched (ADR-019
keeps that time on a cache hit), which is already a log of what the
registry said and when.

A dbt snapshot records state when `dbt snapshot` runs and stamps it with
that run's time. The one NPPES batch landed holds lookups fetched over
three days (2026-11-08 16:52 to 2026-11-11 22:10 UTC); a snapshot would
give them all a single time.

## Decision

int_providers__history derives the SCD2 from every successful NPPES lookup
in the landing zone.

- Tracked attributes (a snapshot's check columns): nppes_found,
  taxonomy_code, location_state and location_postal_code (5 digits). Not
  the taxonomy description, which NUCC rewords; not specialty_category,
  because a mapping change is not a provider change; not street or city,
  because reformatting an address is not a move.
- Grain: one row per NPI per version. A version starts wherever a tracked
  attribute differs from the previous observation, so A, B, A is three
  versions. valid_from is the version's first fetch time; valid_to is the
  next version's valid_from, null for the current version.
- An observation is a successful lookup, a hit or a miss. A failed lookup
  is ignored, the rule staging applies (ADR-021). A cached lookup landed
  twice keeps its fetch time and counts once.
- A miss is a version with nppes_found false, dated when the NPI was first
  seen missing, with no explanation implied. This answers the S20
  objection.
- Materialised as a table, unlike the other intermediates, because the
  range joins downstream read it repeatedly.
- A fact dated before a provider's first observation joins to the earliest
  version, flagged as backfilled (dim_provider at S31, fct_claims at S32).
  The earliest version is the observation nearest the fact.

## Evidence

- A unit test with five crafted NPIs over four batches covers a change and
  a change back, a failure between two hits, a deactivation, a duplicate
  landing and an untracked city change.
- Mutations, each run alone: ignoring taxonomy changes collapsed
  1000000001 to one version; counting failed lookups split 1000000002 into
  three. Both failed the unit test. Emptying the history failed only the
  cross-check against staging, with 2,239 rows; the other six data tests
  pass on an empty table.
- Today's data: one batch, 2,239 versions for 2,239 NPIs, history from
  2026-11-08.

## How wrong the backfill can be

No fact row in this project falls inside the history. Claims are 2013 to
2024, patient events 2019 to 2023, and the history starts in November 2026,
so every fact row joins to a backfilled version. Measured at S30:

| Measure | Prescribers | Share |
|---|---|---|
| Found in NPPES; state in their last CMS year differs from NPPES today | 87 of 2,220 | 3.9% |
| In two or more CMS years; state changed within those years | 103 of 1,544 | 6.7% |
| In two or more CMS years; CMS prescriber type changed within those years | 236 of 1,544 | 15.3% |

The state figures estimate how often a backfilled location is wrong, and
the error grows with the gap between claim year and lookup. The type
figure is an upper bound on specialty change: CMS's label is noisy, and not
every relabelling is a change of practice.

## Consequences

- The history is rebuildable from the landing zone on any target and is
  the same on DuckDB and Snowflake.
- The SCD2 logic is unit-tested. dbt supports unit tests on SQL models
  only, so a snapshot could not have one.
- Phase 4 keeps every NPPES batch in Snowflake, appended rather than
  merged into current state. This amends ADR-021's NPPES MERGE line, and
  staging's NPPES rule is no longer a no-op on Snowflake. There is no
  SNAPSHOTS schema; the snapshot config and transform/snapshots/ are
  removed.
- The as-at join changes no number in this project. New versions appear
  only when lookups are fetched again: the cache's 30-day TTL means the
  first lookups can be refetched from 2026-12-08, and the daily schedule
  (S44) refreshes each NPI about monthly after that. The docs and the
  migration write-up say so rather than imply otherwise.
- fct_claims keeps CMS's prescriber type and state from each claim row
  (S32), so a year-accurate value stays available where the backfill is
  wrong.
- The history is rebuilt from every landed lookup on each build. Cheap at
  2,239 rows per batch; make it incremental if batches multiply.

## Rejected alternatives

- **A dbt snapshot of NPPES (the spec's snap_provider).** It stamps run
  times rather than fetch times, holds the only copy of its history, cannot
  be unit-tested, and would give DuckDB and Snowflake different histories.
- **History from CMS attributes (the S14 decision).** It has real versions
  in today's data, but it versions an attribute no analysis has read since
  S29, and the year-accurate CMS values already arrive on every claim row.
  A dimension rebuilt from them would hand the fact its own columns back,
  with rules to invent for the years a prescriber has no PAH claims.
- **Current NPPES state only.** Simplest, and every number today matches
  it, but it drops the spec's as-at join and loses the date of a
  deactivation.
