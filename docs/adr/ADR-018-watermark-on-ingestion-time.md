# ADR-018: Incremental extraction watermarks on ingestion time, not event time

## Status

Accepted 2026-11-09. Corrects the plan item at `docs/plan.md` S18, which
specifies an `event_timestamp` high-water mark.

## Context

The Postgres application source is extracted incrementally: each run reads
rows added since the last successful run, using the checkpoint store from
plan S9. The checkpoint holds a single watermark value, and the extraction
query filters on it.

The question is which column the watermark reads.

The synthetic schema carries two timestamps per event, and they answer
different questions:

| Column | Meaning |
|---|---|
| `event_timestamp` | When the clinical event happened |
| `created_at` | When the row arrived in this database |

`event_timestamp` is the obvious choice and the one the plan specified. It is
the business-meaningful date, it is what every downstream model groups by, and
it reads naturally: "give me everything since the last event I saw."

It is also wrong, and wrong in a way that loses data silently.

## The failure

Business timestamps are not monotonic with respect to visibility. A row can be
inserted today describing something that happened last week.

In this domain that is the normal case rather than an edge case:

- **Adjudication lag.** A pharmacy claim is dispensed on Friday and recorded
  by the payer on Tuesday.
- **Backdated corrections.** A fill is re-keyed with a corrected date.
- **Batch uploads.** A site uploads a month of activity at once.

With a watermark on `event_timestamp`:

1. Extraction runs on Monday, sees events up to Friday, sets the watermark to
   Friday.
2. On Tuesday the payer inserts Friday's dispense.
3. The next extraction asks for `event_timestamp > Friday`. The row does not
   qualify.
4. It is never extracted. Not retried, not flagged, not counted.

Nothing errors. The pipeline is green, the checkpoint advances, the row counts
look plausible, and a share of claims is simply absent from the warehouse.
This is the failure shape of ADR-010 and a close relative of the Auto Loader
checkpoint bug carried over from the Databricks build: a correct-looking
mechanism operating on the wrong input.

## Decision

**Watermark on `created_at`, a database-generated ingestion timestamp. Never
on `event_timestamp`.**

`created_at` is monotonic with respect to visibility by construction: a row
cannot become visible before it exists. Filtering on it therefore cannot skip
a row, whatever date the business event carries.

Three requirements follow and are part of the decision:

1. **`created_at` is set by the database**, via `DEFAULT now()`, never by the
   application. An application-supplied value reintroduces the problem, since
   the application can supply anything.
2. **`created_at` is never updated backwards.** For mutable rows, `patients`
   carries `updated_at` and incremental extraction of that table watermarks on
   it instead. `patient_events` is append-only.
3. **The watermark column is indexed**, leading: `(created_at, event_id)`. It
   is the predicate every incremental run filters on.

`event_timestamp` remains in the data and is what every downstream model
groups, partitions and reports by. It is a business attribute, not a
mechanism.

## The remaining hazard: commit order against timestamp order

`created_at DEFAULT now()` is not quite sufficient on its own, and the gap is
worth naming because it is invisible until it bites.

In PostgreSQL, `now()` is `transaction_timestamp()` — the time the transaction
*started*, not the time it committed. So:

1. A long transaction starts at 10:00:00 and inserts rows stamped 10:00:00.
2. An extraction runs at 10:00:05, sees nothing from that transaction because
   it has not committed, and advances the watermark to 10:00:05.
3. The long transaction commits at 10:00:09.
4. Those rows carry `created_at` of 10:00:00, which is behind the watermark.
   They are never extracted.

The same class of problem as the one this ADR exists to solve, one layer down.

Mitigation: **extract with a safety lag.** The incremental query reads rows
with `created_at` strictly less than `now() - interval '1 minute'`, and the
watermark advances only to that bound. Any transaction shorter than the lag is
safely captured. The cost is that the most recent minute of data arrives on
the following run, which is irrelevant for a daily pipeline.

`clock_timestamp()` instead of `now()` narrows the window to the commit lag
but does not close it, so it is not a substitute.

## Rejected alternatives

**Watermark on `event_timestamp`.** The plan's original choice. Loses
late-arriving rows permanently and silently.

**Watermark on `event_timestamp` minus a lookback window**, e.g.
`max(event_timestamp) - 3 days`. The common workaround. It bounds the loss
rather than eliminating it — anything later than the window is still lost —
and it requires an idempotent merge downstream to absorb the re-processed
overlap. Since the merge is idempotent anyway and `created_at` is exact and
already available, this trades correctness for nothing.

**Full refresh every run.** Correct, and viable at 77,000 rows. Rejected
because incremental extraction against a watermark is one of the things this
project exists to demonstrate, and because it does not scale with the data it
is standing in for.

**Logical replication or CDC.** The right answer at scale: it reads the WAL in
commit order and has neither of the problems above. Rejected as disproportionate
here — it needs a replication slot, a running consumer, and slot-lag monitoring,
against a source that produces tens of thousands of rows.

## Consequences

**Downstream is unaffected.** Models group, partition and report by
`event_timestamp` as they always would. The watermark choice is invisible
outside the extractor.

**Both columns must survive into the warehouse.** `created_at` is not a
staging artifact to be dropped: reconciling "what did the extractor see when"
requires it, and so does re-deriving the watermark if the checkpoint is lost.

**The checkpoint still advances only on success.** This decision determines
*which value* the watermark takes; the guarantee that it is written only after
the batch has landed comes from the checkpoint store and is unchanged.

**A test is owed.** Insert a row whose `event_timestamp` is well behind the
current watermark but whose `created_at` is ahead of it, run an incremental
extraction, and assert the row is picked up. It is the only test that
distinguishes this decision from the one it replaces, and without it the two
implementations are indistinguishable on well-behaved data.
