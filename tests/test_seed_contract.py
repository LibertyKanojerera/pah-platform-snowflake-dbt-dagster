"""drug_vocabulary.csv has two readers: the ingest CLI and dbt.

dbt owns every content rule (transform/seeds/_seeds.yml). This test owns only
structure: the CSV header the CLI reads must be exactly the column list the
dbt contract declares, in order. Whichever side changes first, the other
fails here, rather than the two definitions drifting apart quietly.
"""

from __future__ import annotations

import csv

import yaml

from ingest.sources.cms_part_d import DEFAULT_VOCABULARY_PATH


def test_drug_vocabulary_header_matches_the_dbt_contract() -> None:
    contract = yaml.safe_load(
        (DEFAULT_VOCABULARY_PATH.parent / "_seeds.yml").read_text()
    )
    seed = next(s for s in contract["seeds"] if s["name"] == "drug_vocabulary")
    declared = [column["name"] for column in seed["columns"]]

    with DEFAULT_VOCABULARY_PATH.open(newline="") as handle:
        header = next(csv.reader(handle))

    assert header == declared
