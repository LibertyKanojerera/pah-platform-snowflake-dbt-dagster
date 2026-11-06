# Sources

## CMS Medicare Part D Prescribers - by Provider and Drug

Reconnaissance performed 2026-11-01. Everything below was observed against the
live API, not taken from documentation.

### Catalog and discovery

There is **one dataset UUID per release**, with no version parameter. The
extractor resolves them at runtime from the DCAT catalog at
`https://data.cms.gov/data.json` rather than hardcoding the table below, so a
new release appears without a code change. The same query is what the Dagster
sensor watches at plan S44.

```bash
curl -s https://data.cms.gov/data.json \
  | jq -r '.dataset[]
      | select(.title | test("Part D Prescribers - by Provider and Drug"; "i"))
      | select(.distribution[]?.accessURL != null)
      | "\(.title)\t\(.distribution[] | select(.accessURL != null) | .accessURL)"' \
  | sort -u
```

Snapshot taken 2026-11-01:

| Title date | UUID |
|---|---|
| 2013-12-31 | `c6905d43-45de-470d-897c-9ed8e75e256d` |
| 2014-12-31 | `0779bc8d-18dd-40b8-9d61-7addc8b0daf1` |
| 2015-12-31 | `1d650894-8afe-4056-ba31-a85cb0e3cee6` |
| 2016-12-31 | `25106f9d-0eb8-4ba7-b237-486ee87d910a` |
| 2017-12-31 | `05f108dd-76c4-49f4-9fdc-788d8f4251ec` |
| 2018-12-31 | `802fe556-311f-4962-8d75-d5f4ff405884` |
| 2019-12-31 | `2a6705e6-7a1e-460c-ba22-35249a531918` |
| 2020-12-31 | `7795fe20-e80e-435a-a9ed-d2d65e05feeb` |
| 2021-12-31 | `f68114ed-f854-4ffc-9c6e-ed78b5e2f8d0` |
| 2022-12-30 | `b101b457-ffa4-49bb-8fd9-27c1266086e2` |
| 2023-12-31 | `e54db557-cd82-4e91-a0fe-61aad5865d69` |
| 2024-12-01 (A) | `9552739e-3d05-4c1b-8eff-ecabf391e2e5` |
| 2024-12-01 (B) | `d5aa71a8-dcc0-4570-8bcf-bd39deac69fe` |

Endpoint pattern:

```
https://data.cms.gov/data-api/v1/dataset/<uuid>/data
https://data.cms.gov/data-api/v1/dataset/<uuid>/data/stats
```

Two oddities in that table, both of which would break a naive date parser:
2022 ends `12-30`, not `12-31`; and **two distinct UUIDs share the title date
`2024-12-01`**. See Open questions.

### Response envelope

A **bare JSON array** of row objects. No wrapper, no pagination metadata, no
`next` link. Row count must be inferred from `length` and termination from a
short page or the `/data/stats` total.

### Every value is a string

`"Tot_Clms": "20"`, `"Tot_Drug_Cst": "5994.01"`, `"Prscrbr_State_FIPS": "24"`.
Numerics, counts and codes all arrive quoted.

Land them as strings, preserving raw fidelity, and cast in dbt staging. The
casts must handle the empty-string suppression sentinel explicitly, because
`''` is not `NULL` in either DuckDB or Snowflake and `TRY_CAST('' AS INTEGER)`
behaviour must be verified rather than assumed.

### Columns

| Column | Notes |
|---|---|
| `Prscrbr_NPI` | String. Join key to NPPES. Luhn-checkable. |
| `Prscrbr_Last_Org_Name` | Individual surname or organisation name |
| `Prscrbr_First_Name` | Empty for organisations |
| `Prscrbr_City` | |
| `Prscrbr_State_Abrvtn` | Two-letter |
| `Prscrbr_State_FIPS` | Zero-padded string; do not cast to int |
| `Prscrbr_Type` | Claim specialty, e.g. "Pulmonary Disease" |
| `Prscrbr_Type_Src` | Provenance of the specialty, e.g. "Claim-Specialty" |
| `Brnd_Name` | **See brand naming below** |
| `Gnrc_Name` | **See brand naming below** |
| `Tot_Clms` | |
| `Tot_30day_Fills` | |
| `Tot_Day_Suply` | |
| `Tot_Drug_Cst` | Decimal as string |
| `Tot_Benes` | Blank when suppressed |
| `GE65_Sprsn_Flag` | |
| `GE65_Tot_Clms` | |
| `GE65_Tot_30day_Fills` | |
| `GE65_Tot_Drug_Cst` | |
| `GE65_Tot_Day_Suply` | |
| `GE65_Bene_Sprsn_Flag` | |
| `GE65_Tot_Benes` | Blank when suppressed |

Casing is `Snake_Title_Case` and is not stable across tools. The Databricks
build lost a column to case-insensitive resolution (ADR-010); treat the names
as exact.

### Brand and generic naming

The values are not the molecule names:

| Expected | Actual observed |
|---|---|
| `Orenitram` | `Orenitram Er` |
| `Treprostinil` | `Treprostinil Diolamine` |

Exact equality on either expected value returns zero rows. The pipeline would
run green and report no PAH prescribing in the United States.

This is the same failure as ADR-002 in the Databricks build, where naive
equality on brand name silently dropped roughly 450 Orenitram rows. The
longest-prefix matcher carried forward in that ADR is the resolution, and the
test at plan S28 must assert it still catches these.

Molecules and their brands, to be verified individually against the API:

| Molecule | Brands |
|---|---|
| treprostinil | Remodulin, Tyvaso, Orenitram (observed as `Orenitram Er`) |
| selexipag | Uptravi |
| iloprost | Ventavis |
| epoprostenol | Flolan, Veletri |

Selexipag was approved in late 2015, so its absence from earlier release years
is a true negative, not a gap. The test at plan S36 must not treat it as one.

### Suppression

CMS suppresses low-count values. Three distinct states observed, **none of
them null**:

| Sentinel | Meaning (to confirm) |
|---|---|
| `""` | Not suppressed, when in a `_Sprsn_Flag` column |
| `""` | Suppressed, when in a `Tot_Benes` / `GE65_Tot_Benes` column |
| `"#"` | Observed alongside `GE65_Tot_Clms: "0"` |
| `"*"` | Observed alongside `GE65_Tot_Clms: "25"` |

Observed row: `Tot_Clms: "23"` with `Tot_Benes: ""` and
`GE65_Bene_Sprsn_Flag: "#"`. So a row can carry claims and costs while its
beneficiary count is withheld.

The same empty string therefore means "not suppressed" in a flag column and
"suppressed" in a count column. Any staging logic that checks `IS NULL` will
classify every row as unsuppressed and be silently wrong.

For a rare disease a large majority of provider-drug pairs fall below the
reporting threshold, so this is not an edge case. Plan S36 asserts the
suppression rate sits inside an expected band.

### Pagination

| Parameter | Behaviour |
|---|---|
| `size` | Page size. **Hard ceiling of 6500**, observed. |
| `offset` | Row offset |

Measured: `size=5000` returns 5000; `size=6500` returns 6500; `size=6600`
returns 6500; `size=8000` returns 6500. The cap is a row count, not a byte
budget, since the ceiling held constant across requests.

Total rows, UUID `d5aa71a8`:

```json
{"found_rows": 28023892, "total_rows": 28023892}
```

Twenty-eight million rows per release. Server-side filtering is mandatory, not
an optimisation.

`/data/stats` returns two counts and they are not interchangeable:
`found_rows` honours the filter, `total_rows` is the unfiltered table size.
Observed with `filter[Brnd_Name]=Orenitram Er`: found_rows 450,
total_rows 28,023,892. Paginating against total_rows would issue ~4,300
requests to yield 450 rows.

Ordering is stable: the same request returns identical rows twice, and
overlapping windows agree (rows 25-49 of offset=0 match rows 0-24 of
offset=25). Offset pagination is therefore safe without an explicit sort.

### Filtering: CONFIRMED

`filter[Column]=Value` works. Requires `curl -g` at the shell, since bash and
curl both treat `[...]` as a glob; no issue from Python, where requests
URL-encodes the brackets.

Verified by contrast, not by assumption: unfiltered, the first two rows are
Abrysvo and Boostrix Tdap; with `filter[Brnd_Name]=Orenitram%20Er`, both rows
are Orenitram Er.

`/data/stats` also accepts the filter, so a run knows its total up front.

`keyword=` works as a broader fallback.
`column=` does not accumulate across repeated parameters; unresolved.

### Open questions for plan S11

1. Does `filter[...]` work with `curl -g`? If not, is `keyword=` selective
   enough, or does filtering have to happen client-side across 28M rows?
   If client-side, the extractor design changes materially.
2. Filter on `Brnd_Name` or `Gnrc_Name`? Generic is one request per molecule
   and misses nothing when a new brand launches; brand is one request per
   brand and is unambiguous. Decide and record as an ADR.
3. **Two UUIDs share the title date `2024-12-01`.** Which is current, and does
   the title date mean the data period or the release date? The 2013-2023
   entries look like data periods, the 2024 pair may be release dates. Until
   this is settled the year-to-UUID mapping is not trustworthy.
4. Is the result set stably ordered without an explicit sort? If not, offset
   pagination can skip or duplicate rows between pages and the idempotency
   test at plan S13 will fail intermittently in a way that looks like a code
   bug. Verify before writing the pagination loop.
5. Rate limits: unobserved. No `Retry-After` or `X-RateLimit-*` headers seen,
   but no load has been applied.

Duplicate was verified identical by row count rather than assumed, and that temporal disagreement is the trigger for refusing.

### Sample

`data/samples/cms_part_d_sample.json`, captured 2026-11-01 from UUID
`d5aa71a8-dcc0-4570-8bcf-bd39deac69fe` via `keyword=Orenitram`.

### Counts, ordering and grain

`/data/stats` returns two counts and they are not interchangeable:
`found_rows` honours the filter, `total_rows` is the unfiltered table size.
With `filter[Brnd_Name]=Orenitram Er` on the 2024 release: found_rows 450,
total_rows 28,023,892. Paginating against total_rows would issue ~4,300
requests to yield 450 rows.

Ordering is stable. The same request returns identical rows twice, and
overlapping windows agree: rows 25-49 of offset=0 match rows 0-24 of
offset=25. Offset pagination is safe without an explicit sort.

Grain: for one brand in one release year, Prscrbr_NPI is unique. 2023
Orenitram Er returned 391 rows and 391 distinct NPIs. Candidate natural key
is (Prscrbr_NPI, Brnd_Name, release_year); confirm against a second brand
before S32 relies on it.

Duplicate catalog registrations exist. 2024-12-01 is registered under two
UUIDs whose data is identical (both report found_rows 450). Verified by row
count rather than assumed. resolve_dataset() collapses duplicates that agree
on `temporal` and logs a warning; it still refuses when `temporal` differs,
since that would mean two genuinely different cuts.

### Drug vocabulary

The stored `Gnrc_Name` and `Brnd_Name` values do not match the molecule names,
and they are not stable across releases. The full vocabulary was established
by sweeping all 13 releases with `keyword=<molecule stem>` and collecting
distinct pairs. Twelve pairs across nine distinct generic names:

| Gnrc_Name | Brnd_Name | Molecule | Route | Type |
|---|---|---|---|---|
| Epoprostenol Sodium | Epoprostenol Sodium | epoprostenol | parenteral | drug |
| Epoprostenol Sodium | Veletri | epoprostenol | parenteral | drug |
| Epoprostenol Sodium (Arginine) | Veletri | epoprostenol | parenteral | drug |
| Epoprostenol Sodium (Glycine) | Flolan | epoprostenol | parenteral | drug |
| Iloprost Tromethamine | Ventavis | iloprost | inhaled | drug |
| Selexipag | Uptravi | selexipag | oral | drug |
| Treprostinil | Tyvaso Dpi | treprostinil | inhaled | drug |
| Treprostinil Diolamine | Orenitram Er | treprostinil | oral | drug |
| Treprostinil Sodium | Remodulin | treprostinil | parenteral | drug |
| Treprostinil Sodium | Treprostinil | treprostinil | parenteral | drug |
| Treprostinil/Neb Accessories | Tyvaso | treprostinil | inhaled | drug |
| Treprostinil/Neb Accessories | Tyvaso Refill Kit | treprostinil | inhaled | accessory |

Maintained as `transform/seeds/drug_vocabulary.csv`, read by both the Python
extractor and dbt. Distinct `gnrc_name` values are the filter predicates;
`brnd_name` determines route and product type. See ADR-015.

#### The vocabulary is not stable across releases

Four patterns in the table above would each break a naive implementation.

**A generic name can move between releases.** In the 2013 release, Tyvaso is
filed under `Treprostinil/Neb Accessories`. By 2023 the inhaled product is
`Tyvaso Dpi` under `Treprostinil`, while `Treprostinil/Neb Accessories` now
carries `Tyvaso Refill Kit`. A vocabulary built from either year alone would
silently omit the inhaled route from the other. This is the concrete evidence
for the unknown-value sweep in ADR-015.

**One brand can carry several generic names.** Veletri appears under both
`Epoprostenol Sodium` and `Epoprostenol Sodium (Arginine)` in different
releases. Epoprostenol is formulated with a diluent — glycine for Flolan,
arginine for Veletri — and CMS has not been consistent about including it.
This is why the vocabulary grain is the pair, not either column alone.

**Brand name sometimes equals generic name.** `Treprostinil Sodium` /
`Treprostinil` and `Epoprostenol Sodium` / `Epoprostenol Sodium` are
authorized generics. These read like data errors and are not.

**`Tyvaso Refill Kit` is not a drug.** It is nebulizer supplies billed under
Part D. Counting its claims as prescribing events double-counts any patient
receiving both the drug and its accessories, inflating the measure research
question one depends on. Hence the `product_type` column, which keys off
brand rather than generic, since `Treprostinil/Neb Accessories` is a drug in
2013 and an accessory in 2023. Accessories must be excluded from claim counts
at the staging or intermediate layer (plan S25).

#### Molecule validity windows

Not every molecule appears in every release, and both directions are true
negatives rather than gaps:

| Molecule | Note |
|---|---|
| selexipag | Approved late 2015; absent from earlier releases |
| iloprost | Present in 2013, absent from 2023; discontinued or below the reporting threshold |

The completeness test at plan S36 therefore needs a per-molecule validity
window, not a blanket assertion that all four molecules appear every year.

#### Sweep method

Run against every release UUID, for each molecule stem:

```bash
curl -sg "https://data.cms.gov/data-api/v1/dataset/$UUID/data?size=6500&keyword=$STEM" \
  | jq -r '.[] | "\(.Gnrc_Name)\t\(.Brnd_Name)"' | sort -u
```

`keyword` matches broadly and can return unrelated drugs whose names contain
a stem as a substring, so output requires human review before entering the
seed. The extractor runs the same sweep on every extraction and raises on any
pair absent from the vocabulary.

`aws configure export-credentials` sets AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY and AWS_SESSION_TOKEN in the shell. Those take
precedence over AWS_PROFILE in boto3, so once they expire, Python writes fail
with "Credentials were refreshed, but the refreshed credentials are still
expired" even though the SSO session is healthy. Unset all three before
running anything that writes, or export them in a subshell.

## NPPES NPI Registry

Reconnaissance performed 2026-11-04 against the live API.

### Endpoint and version
### Not found: resolved

A well-formed but unassigned NPI returns **HTTP 200** with:

```json
{"result_count": 0, "results": []}
```

Not a 404, not an `Errors` object. `ingest/core/http.py` therefore returns it
as an ordinary success, and the miss is invisible at the transport layer. The
NPPES module must branch on `result_count` itself; the dead-letter path at
plan S16 lives there, not in the HTTP client.

Three distinct NPPES outcomes, only one of which the HTTP layer can see:

| Outcome | HTTP | Body |
|---|---|---|
| Found | 200 | `result_count: 1`, populated `results` |
| Not found | 200 | `result_count: 0`, empty `results` |
| Bad request | not captured | `{"Errors": [...]}` |

### Enumeration type: all individuals

Every one of the 11,830 landed CMS rows carries a non-empty
`Prscrbr_First_Name` (lengths 3 to 13, no nulls, no blanks), so all 1,100
distinct NPIs are individuals. PAH is specialist-prescribed and no
organisational NPIs appear in the prescriber set.

The `NPI-2` branch in `nppes.py` is therefore written defensively but is
untested by this data. Say so rather than claiming coverage it does not have.

### Landing zone contains multiple batches per release

Every extraction writes a new `batch=<uuid>` prefix rather than overwriting,
so the landing zone accumulates one copy of a release per run. As of
2026-11-06 the 2023 release is present seven times (6 integration-test runs
plus the S12 landing), giving 11,830 rows for 1,690 distinct source rows.

Consequence for plan S23: a `read_parquet` glob over the landing zone must
deduplicate on the natural key (Prscrbr_NPI, Brnd_Name, _cms_release_year) or
select the most recent batch per release year. The staging layer owns this;
the landing zone is deliberately append-only.
