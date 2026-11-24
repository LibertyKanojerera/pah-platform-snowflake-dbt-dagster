# ADR-022: Route is resolved by an exact pair join; the longest-prefix matcher is retired

Status: Accepted, plan S28
Supersedes, for the port: ADR-002 of the Databricks build (longest-prefix
brand matching)
Amends: the spec's "keep the longest-prefix matcher as documented
fallback" and its longest_prefix_match() macro
Related: ADR-015 (vocabulary sweep), ADR-010 (prove a test can fail)

## Context

The Databricks build resolved route from a reference of normalised brand
names (Orenitram, Tyvaso). CMS stores variants (Orenitram Er, Tyvaso Dpi,
Tyvaso Refill Kit), so exact equality against that reference dropped
roughly 450 oral Orenitram rows, and ADR-002 replaced equality with
longest-prefix matching. The port spec kept the matcher as a fallback.

The port changed the inputs. drug_vocabulary (ADR-015) is keyed on the
exact stored (generic, brand) pair, swept from every release. Ingest
raises on a pair it does not contain, and a dbt test checks every landed
pair against it. At S28, 12 pairs cover all 10,871 claim rows and 398,062
claims. Data year 2024 holds exactly 450 Orenitram Er rows.

## Decision

Molecule, route and product type come from an exact left join on both
stored strings, in int_drugs__route_resolved. There is no longest-prefix
matcher and no longest_prefix_match() macro. Three tests hold the join to
that:

- not_null on molecule, route and product type: an unknown pair fails
  instead of disappearing.
- assert_route_resolution_keeps_every_claim_row: resolved rows add up to
  every staged row.
- assert_drugs_resolve_to_their_own_vocabulary_row: each drug carries the
  attributes of its own pair, not of a neighbour.

## Evidence

The S28 mutation joined on the brand's first word, ADR-002's mistake
mirrored. It failed in both ways a normalised match fails:

- It dropped 4,985 rows (Orenitram Er 3,777, Tyvaso Dpi 1,206, Epoprostenol
  Sodium 2). not_null and the row-count test caught this.
- It matched Tyvaso Refill Kit to the Tyvaso drug row, which shares its
  generic string, and counted 79 accessory rows (1,028 claims) as an
  inhaled drug with every count still adding up. Only the own-row test,
  added because of this result, catches it. The Databricks build made the
  same assignment.

## Consequences

- No claim row is assigned a route by guesswork, and none is dropped in
  silence.
- A new brand or a renamed pair stops ingestion until a person adds a
  vocabulary row, deciding its route and product type deliberately. That
  cost is intended.
- The pair carries meaning that neither string carries alone.
  Treprostinil/Neb Accessories is Tyvaso, a drug, in 2013 to 2015, and
  Tyvaso Refill Kit, an accessory, in 2016 to 2024. Veletri is recorded
  under Epoprostenol Sodium (Arginine) to 2019 and Epoprostenol Sodium from
  2020. Generic injections carry the brand names Treprostinil and
  Epoprostenol Sodium, which say nothing about route.
- Accessories stay in staging and the claims fact. The intermediate layer
  flags them by product type, and claim counts leave them out at the
  aggregate (plan S34).
- RxNorm or openFDA, the spec's optional fourth source, remains the
  upgrade path if the vocabulary grows beyond what a person can curate.

## Rejected alternatives

- **Keep the matcher as a fallback.** It could never fire, because ingest
  refuses an unknown pair before dbt sees it. If it could fire it would
  guess, and the Refill Kit mutation shows what a wrong guess looks like:
  plausible, consistent, and invisible to count checks.
- **Longest-prefix matching as the primary method.** It resolves today's
  rows, but with a Tyvaso entry and no Refill Kit entry it makes exactly
  the accessory-as-drug assignment above while passing every count test.
  Making it safe needs an entry per stored pair, which is the vocabulary.
- **Exact equality on brand alone.** ADR-002's original drop, and still
  ambiguous where one brand spans two generic strings (Veletri).
