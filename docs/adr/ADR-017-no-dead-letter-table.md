# ADR-017: No dead-letter table for NPPES failures

## Status

Accepted 2026-11-08. Deviates from the plan item at `docs/plan.md` S16, which
specified a dead-letter table.

## Context

NPPES enrichment issues one request per NPI across roughly 1,100 distinct
prescribers. Reconnaissance established three possible outcomes per lookup,
and they are not interchangeable:

| Outcome | Signal | Meaning |
|---|---|---|
| Found | HTTP 200, `result_count: 1` | The provider exists |
| Miss | HTTP 200, `result_count: 0` | No such NPI in the registry |
| Failure | exception after retries | The lookup did not happen |

A miss is a fact about the world. A failure is a fact about the run. Five of
1,100 NPIs are genuine misses; failures have so far been simulated only.

Before this decision, a single failure propagated and aborted the whole run,
discarding 1,099 successful lookups. That is clearly wrong: CMS prescribing is
the fact and NPPES is the dimension, so an unenriched provider is a nullable
join, not a pipeline error.

The conventional remedy is a dead-letter table or queue, and it does two jobs:

1. **Record** which items failed and why, so the failures are not lost.
2. **Drive retry**, giving a later process something to read and re-attempt.

The plan called for one. Having built the caching layer, neither job turns out
to need a table.

## Decision

**No dead-letter table. The error is a column on the landed row, and failures
are simply not cached.**

Concretely:

- Every landed NPPES row carries `nppes_error`, empty on success and on a
  miss, populated with the exception text on a failure. The three outcomes
  stay distinguishable in the data: `nppes_found` separates found from the
  rest, `nppes_error` separates miss from failure.
- `JsonCache` stores hits and misses. **Failures are never written to it.**
- A per-NPI exception is caught, logged, recorded on the row, and the run
  continues.
- A failure-rate ceiling of 5%, checked after a minimum of 50 attempts,
  aborts the run rather than landing a batch that is mostly holes.

Job 1 is done by the row: `nppes_error <> ''` identifies every failure, in the
lake, queryable from dbt, carrying the batch id, the extraction timestamp and
the full provenance of the row it belongs to.

Job 2 is done by the caching rule. Because failures are not cached, the next
run retries them automatically. There is nothing to drive, because the retry
is the default behaviour rather than a separate process.

The dead letter is a query:

```sql
SELECT npi, nppes_error, _batch_id, _ingested_at
FROM nppes
WHERE nppes_error <> ''
```

## Rejected alternatives

**A separate dead-letter table.** Duplicates state that already lives in the
landed rows, and introduces a second source of truth that can disagree with
the first. It would need its own lifecycle, its own cleanup, its own place in
the Snowflake DDL and its own tests. Every one of those is cost against a
capability the rows already provide.

**Abort the whole run on any failure.** The previous behaviour. Discards up to
1,099 good lookups because one provider's request timed out, and makes the
pipeline's reliability the product of 1,100 independent probabilities.

**Tolerate failures with no ceiling.** Would let a run in which every lookup
failed land a batch with a valid manifest and a complete-looking row count,
where every provider is simply unenriched. Indistinguishable from a period in
which the registry genuinely had nothing. This is the failure mode of
ADR-010, and the 5% ceiling exists specifically to prevent it.

**Cache failures alongside hits and misses.** Simpler to implement and badly
wrong: a transient timeout would become a permanent hole for the length of
the TTL, and the hole would look exactly like a legitimately unfound
provider.

**Retry inline, within the same run, until success.** Conflates two timescales.
`http.py` already retries transient transport failures with backoff; what
reaches this layer has already exhausted that. Retrying further inside one run
turns a four-minute job into an unbounded one for no additional recall.

## When a dead-letter table would be right

This decision is specific to these circumstances and should not be read as a
general objection to the pattern. A dead-letter store earns its place when:

- The source is **destructive** — a message consumed off a queue cannot be
  re-read, so a failure that is not captured is lost permanently.
- The input set is **not reproducible**. Here the NPI list is derived from
  landed CMS data with one SQL statement, so "what should have been fetched"
  is always recoverable.
- Retry must run on a **different schedule** from the main job, rather than
  falling out of the next ordinary run.
- Failures need **human triage** before re-attempt.

None of those hold. The source is a stable public API keyed by NPI, the input
is reproducible, a full run costs four minutes, and a failure needs no
judgment to retry.

## Consequences

**`nppes_error` must exist on every row**, set to `''` on success, or the
dead-letter query has to reason about nulls and the distinction between miss
and failure blurs.

**The failure-rate ceiling becomes load-bearing.** With no queue accumulating
failures for inspection, the abort is the only thing standing between a
systemically broken run and a hollow batch that reads as complete. It is not
an optimisation and must not be relaxed without replacing it.

**The manifest carries all three counts** — `found`, `missed`, `failed` —
making `requested == found + missed + failed` an invariant the row-count band
test at plan S36 can assert. A batch that violates it has lost rows somewhere
between lookup and landing.

**Revisit if a streaming source is added.** Plan S18's Postgres extractor
reads a re-readable table, so the same reasoning holds. A genuine event stream
would not satisfy the reproducibility condition and would need the table this
decision declines to build.
