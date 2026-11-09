"""Synthetic PAH patient cohort with a known data-generating process.

The point of this module is that the hazard is designed, not incidental. Each
patient's time to discontinuation is drawn from an exponential distribution
whose rate is modulated by a proportional-hazards term built from covariates
with coefficients declared in TRUE_COEFFICIENTS below.

That makes the ground truth version-controlled. The Cox model at plan S45 is
then validated by asserting it recovers each coefficient within its confidence
interval, which is a far stronger regression test than a concordance index:
a C-index can look respectable on a model that has learned nothing in
particular, whereas recovering a known log-hazard ratio cannot happen by
accident.

Prescriber NPIs are drawn from the landed CMS set and their specialty from the
landed NPPES enrichment, so the synthetic cohort joins to real data and
specialty genuinely participates in the hazard rather than being decorative.

Deterministic: seeded, so re-running produces an identical cohort and the
idempotency guarantees elsewhere in the pipeline continue to hold.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Sequence

DEFAULT_SEED = 20261109
DEFAULT_PATIENTS = 5_000

STUDY_START = date(2019, 1, 1)
#: Last date a patient can enter. Entrants after this have too little
#: follow-up to contribute, and the gap to STUDY_END produces the
#: administrative censoring that makes the survival analysis non-trivial.
ENROLMENT_END = date(2023, 6, 30)
STUDY_END = date(2023, 12, 31)

#: Median time to discontinuation for the reference patient, in months:
#: oral route, copay tier 1, under 75, non-pulmonology prescriber.
REFERENCE_MEDIAN_MONTHS = 18.0
BASELINE_HAZARD = math.log(2) / REFERENCE_MEDIAN_MONTHS

#: THE GROUND TRUTH. Log hazard ratios. Plan S46 asserts the fitted Cox model
#: recovers each of these within its 95% confidence interval.
#:
#: Directions are clinically defensible rather than arbitrary:
#:   inhaled      four-times-daily nebulisation is burdensome -> worse persistence
#:   parenteral   continuous infusion, intensively managed, life-sustaining ->
#:                better persistence despite being the sicker population
#:   copay        higher out-of-pocket cost -> worse persistence, monotonic
#:   age 75+      frailty and polypharmacy -> worse persistence
#:   pulmonology  specialist management -> better persistence
TRUE_COEFFICIENTS: dict[str, float] = {
    "route_inhaled": 0.25,
    "route_parenteral": -0.35,
    "copay_tier_2": 0.15,
    "copay_tier_3": 0.35,
    "copay_tier_4": 0.55,
    "age_75_plus": 0.30,
    "prescriber_pulmonology": -0.20,
}

#: Matches the route mix observed in the landed 2023 CMS data:
#: oral 1,185, inhaled 450, parenteral 51 of 1,686 drug rows.
ROUTE_WEIGHTS = {"oral": 0.70, "inhaled": 0.27, "parenteral": 0.03}

COPAY_WEIGHTS = {1: 0.30, 2: 0.30, 3: 0.25, 4: 0.15}
AGE_WEIGHTS = {"18-64": 0.35, "65-74": 0.40, "75+": 0.25}

DAYS_PER_FILL = 30


@dataclass(frozen=True)
class Prescriber:
    npi: str
    is_pulmonology: bool
    state: str


@dataclass(frozen=True)
class Patient:
    patient_id: int
    prescriber: Prescriber
    index_date: date
    route: str
    copay_tier: int
    age_band: str
    sex: str
    months_observed: float
    discontinued: bool


def _weighted(rng: random.Random, weights: dict[Any, float]) -> Any:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _linear_predictor(
    *, route: str, copay_tier: int, age_band: str, is_pulmonology: bool
) -> float:
    total = 0.0
    if route != "oral":
        total += TRUE_COEFFICIENTS[f"route_{route}"]
    if copay_tier > 1:
        total += TRUE_COEFFICIENTS[f"copay_tier_{copay_tier}"]
    if age_band == "75+":
        total += TRUE_COEFFICIENTS["age_75_plus"]
    if is_pulmonology:
        total += TRUE_COEFFICIENTS["prescriber_pulmonology"]
    return total


def generate_patients(
    prescribers: Sequence[Prescriber], *, count: int, seed: int
) -> list[Patient]:
    rng = random.Random(seed)
    patients: list[Patient] = []
    enrolment_days = (ENROLMENT_END - STUDY_START).days

    for patient_id in range(1, count + 1):
        prescriber = rng.choice(prescribers)
        index_date = STUDY_START + timedelta(days=rng.randint(0, enrolment_days))
        route = _weighted(rng, ROUTE_WEIGHTS)
        copay_tier = int(_weighted(rng, COPAY_WEIGHTS))
        age_band = _weighted(rng, AGE_WEIGHTS)

        hazard = BASELINE_HAZARD * math.exp(
            _linear_predictor(
                route=route,
                copay_tier=copay_tier,
                age_band=age_band,
                is_pulmonology=prescriber.is_pulmonology,
            )
        )

        # Inverse-transform sampling from the exponential: T = -ln(U) / lambda.
        true_months = -math.log(1.0 - rng.random()) / hazard

        # Administrative censoring at STUDY_END.
        available_months = (STUDY_END - index_date).days / 30.4375
        discontinued = true_months <= available_months

        patients.append(
            Patient(
                patient_id=patient_id,
                prescriber=prescriber,
                index_date=index_date,
                route=route,
                copay_tier=copay_tier,
                age_band=age_band,
                sex=_weighted(rng, {"F": 0.78, "M": 0.22}),  # PAH skews female
                months_observed=min(true_months, available_months),
                discontinued=discontinued,
            )
        )

    return patients


def generate_events(patients: Sequence[Patient]) -> Iterator[dict[str, Any]]:
    """One row per fill, plus a discontinuation row for patients who stopped.

    created_at is set a day or two after event_timestamp, so the incremental
    watermark at plan S18 has something non-degenerate to work with and late
    arrival can be simulated.
    """
    event_id = 0
    for patient in patients:
        days_observed = patient.months_observed * 30.4375
        fills = max(1, int(days_observed // DAYS_PER_FILL))

        for fill_index in range(fills):
            event_id += 1
            event_time = datetime.combine(
                patient.index_date + timedelta(days=fill_index * DAYS_PER_FILL),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            yield {
                "event_id": event_id,
                "patient_id": patient.patient_id,
                "event_type": "fill",
                "event_timestamp": event_time,
                "route": patient.route,
                "days_supply": DAYS_PER_FILL,
                "created_at": event_time + timedelta(days=1),
            }

        if patient.discontinued:
            event_id += 1
            event_time = datetime.combine(
                patient.index_date + timedelta(days=int(days_observed)),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            yield {
                "event_id": event_id,
                "patient_id": patient.patient_id,
                "event_type": "discontinuation",
                "event_timestamp": event_time,
                "route": patient.route,
                "days_supply": None,
                "created_at": event_time + timedelta(days=2),
            }


# --- reading prescribers from the landed lake --------------------------------

PRESCRIBER_QUERY = """
WITH cms AS (
    SELECT DISTINCT
        Prscrbr_NPI            AS npi,
        Prscrbr_State_Abrvtn   AS cms_state
    FROM read_parquet('s3://{bucket}/landing/cms_part_d/*/*/*.parquet')
),
nppes AS (
    SELECT npi, taxonomy_desc, location_state, nppes_found
    FROM read_parquet('s3://{bucket}/landing/nppes/*/*/*.parquet')
    QUALIFY row_number() OVER (PARTITION BY npi ORDER BY _ingested_at DESC) = 1
)
SELECT
    cms.npi,
    coalesce(nppes.taxonomy_desc, '') ILIKE '%pulmonary%'          AS is_pulmonology,
    coalesce(nullif(nppes.location_state, ''), cms.cms_state, '')  AS state
FROM cms
LEFT JOIN nppes USING (npi)
ORDER BY cms.npi
"""


def load_prescribers(bucket: str) -> list[Prescriber]:
    """Prescribers from the landed CMS and NPPES data.

    Requires AWS credentials in the environment. DuckDB's default credential
    chain cannot resolve an SSO profile's cached token, so the secret is
    created against the env chain explicitly. See docs/sources.md.

    The NPPES side deduplicates to the most recent batch per NPI: the landing
    zone is append-only and holds one batch per run. This is the same problem
    the dbt sources must solve at plan S23.
    """
    import os

    import duckdb

    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        raise RuntimeError(
            "AWS credentials not in the environment. Run:\n"
            '  eval "$(aws configure export-credentials '
            '--profile pah-admin --format env)"'
        )

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, CHAIN 'env');")

    rows = con.execute(PRESCRIBER_QUERY.format(bucket=bucket)).fetchall()
    con.close()

    if not rows:
        raise RuntimeError(f"no prescribers found in s3://{bucket}/landing/")

    prescribers = [
        Prescriber(npi=r[0], is_pulmonology=bool(r[1]), state=r[2] or "") for r in rows
    ]

    pulmonology = sum(p.is_pulmonology for p in prescribers)
    print(
        f"{len(prescribers)} prescribers, {pulmonology} pulmonology "
        f"({pulmonology / len(prescribers):.0%})"
    )
    return prescribers


# --- loading into Postgres ---------------------------------------------------


def load_to_postgres(
    patients: Sequence[Patient],
    events: Iterator[dict[str, Any]],
    *,
    truncate: bool,
) -> tuple[int, int]:
    """COPY the cohort in. Returns (patients, events) written.

    COPY rather than executemany: ~75,000 event rows go in under a second
    instead of minutes, and the generator is re-run often enough to care.
    """
    import psycopg

    from ingest.core.config import get_postgres_config

    config = get_postgres_config()
    dsn = (
        f"host={config.host} port={config.port} dbname={config.dbname} "
        f"user={config.user} password={config.password.get_secret_value()}"
    )

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if truncate:
                # Events first: the FK points at patients.
                cur.execute("TRUNCATE patient_events, patients RESTART IDENTITY")

            with cur.copy(
                "COPY patients (patient_id, prescriber_npi, index_date, "
                "initial_route, copay_tier, age_band, sex, prescriber_state) "
                "FROM STDIN"
            ) as copy:
                for p in patients:
                    copy.write_row(
                        (
                            p.patient_id,
                            p.prescriber.npi,
                            p.index_date,
                            p.route,
                            p.copay_tier,
                            p.age_band,
                            p.sex,
                            p.prescriber.state or None,
                        )
                    )

            event_count = 0
            with cur.copy(
                "COPY patient_events (event_id, patient_id, event_type, "
                "event_timestamp, route, days_supply, created_at) FROM STDIN"
            ) as copy:
                for e in events:
                    copy.write_row(
                        (
                            e["event_id"],
                            e["patient_id"],
                            e["event_type"],
                            e["event_timestamp"],
                            e["route"],
                            e["days_supply"],
                            e["created_at"],
                        )
                    )
                    event_count += 1

        conn.commit()

    return len(patients), event_count


# --- entry point -------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default="pah-platform-landing-036999274035")
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS)
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="changing this changes the cohort, which invalidates the "
        "coefficient-recovery test at plan S46",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="clear both tables first; without it a second run violates the "
        "primary key, which is the correct outcome rather than silent "
        "duplication",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    prescribers = load_prescribers(args.bucket)
    patients = generate_patients(prescribers, count=args.patients, seed=args.seed)

    discontinued = sum(p.discontinued for p in patients)
    print(
        f"{len(patients)} patients, {discontinued} discontinued, "
        f"{len(patients) - discontinued} censored "
        f"({1 - discontinued / len(patients):.0%})"
    )

    if args.dry_run:
        print("dry run, nothing written")
        return

    written = load_to_postgres(
        patients, generate_events(patients), truncate=args.truncate
    )
    print(f"wrote {written[0]} patients, {written[1]} events")


if __name__ == "__main__":
    main()
