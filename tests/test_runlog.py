"""Run log semantics.

Two tests carry the design: a checkpoint that fails to commit is logged as
failed, and a clean exit that cannot be recorded truthfully still leaves a
row. Each has a mutation check in the S20 plan: break the code, watch the
test go red, restore.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from ingest.core.landing import LandedPart
from ingest.core.runlog import COLUMNS, DuckDBRunLog, RunLogError
from ingest.core.state import DuckDBStateStore
from tests.conftest import MemoryRunLog

MANIFEST = "landing/demo/dt=2026-11-13/batch=b/_manifest.json"


def _part(rows: int = 3) -> LandedPart:
    return LandedPart(
        key="landing/demo/dt=2026-11-13/batch=b/part-0000.parquet",
        row_count=rows,
        byte_count=100,
        sha256="0" * 64,
    )


# --- outcomes ---------------------------------------------------------------


def test_a_clean_attempt_records_one_success_row() -> None:
    runlog = MemoryRunLog()
    with runlog.attempt("demo") as attempt:
        attempt.rows_read = 3
        attempt.record_landed([_part(3)], manifest_key=MANIFEST)

    assert len(runlog.rows) == 1
    row = runlog.rows[0]
    # The row must carry exactly the table's columns, or the DuckDB insert
    # fails on a KeyError the first time a real run tries to log.
    assert set(row) == set(COLUMNS)
    assert row["status"] == "success"
    assert (row["rows_read"], row["rows_written"], row["parts_written"]) == (3, 3, 1)
    assert row["manifest_key"] == MANIFEST
    assert row["duration_ms"] >= 0
    assert row["finished_at"] >= row["started_at"]
    assert row["error_class"] is None
    assert (row["run_id"], row["triggered_by"]) == ("test-run", "test")


def test_no_new_rows_is_a_row_not_an_absence() -> None:
    """'Ran and found nothing' must be distinguishable from 'did not run'."""
    runlog = MemoryRunLog()
    with runlog.attempt("demo") as attempt:
        attempt.mark_no_new_rows()

    outcomes = [(r["status"], r["rows_written"]) for r in runlog.rows]
    assert outcomes == [("no_new_rows", 0)]


def test_an_exception_records_failed_and_reraises_the_original() -> None:
    runlog = MemoryRunLog()
    with pytest.raises(ValueError, match="boom"):
        with runlog.attempt("demo"):
            raise ValueError("boom")

    assert [r["status"] for r in runlog.rows] == ["failed"]
    assert runlog.rows[0]["error_class"] == "ValueError"
    assert runlog.rows[0]["error_message"] == "boom"


# --- ordering: success means landed AND checkpointed ------------------------


def test_a_committed_checkpoint_is_logged_as_success(tmp_path: Path) -> None:
    store = DuckDBStateStore(str(tmp_path / "state.duckdb"))
    runlog = MemoryRunLog()

    with runlog.checkpointed_attempt(store, "demo") as (attempt, checkpoint):
        attempt.record_landed([_part()], manifest_key=MANIFEST)
        checkpoint.advance("2024", batch_id="b")

    assert [r["status"] for r in runlog.rows] == ["success"]
    saved = store.read("demo")
    assert saved is not None and saved.watermark == "2024"


def test_a_checkpoint_that_fails_to_commit_is_logged_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data landed but the checkpoint did not commit. The row must say
    failed: success means landed AND checkpointed.
    """
    store = DuckDBStateStore(str(tmp_path / "state.duckdb"))
    runlog = MemoryRunLog()

    def broken_persist(_checkpoint: object) -> None:
        raise OSError("simulated state write failure")

    monkeypatch.setattr(store, "_persist", broken_persist)

    with pytest.raises(OSError):
        with runlog.checkpointed_attempt(store, "demo") as (attempt, checkpoint):
            attempt.record_landed([_part()], manifest_key=MANIFEST)
            checkpoint.advance("2024", batch_id="b")

    assert [r["status"] for r in runlog.rows] == ["failed"]
    assert runlog.rows[0]["error_class"] == "OSError"


# --- exits that cannot be recorded truthfully -------------------------------


def test_an_unrecordable_clean_exit_still_writes_a_failed_row() -> None:
    """Every attempt leaves a row, including one that closed without
    record_landed() or mark_no_new_rows().
    """
    runlog = MemoryRunLog()
    with pytest.raises(RunLogError):
        with runlog.attempt("demo"):
            pass

    outcomes = [(r["status"], r["error_class"]) for r in runlog.rows]
    assert outcomes == [("failed", "RunLogError")]


def test_no_new_rows_with_rows_written_is_refused() -> None:
    runlog = MemoryRunLog()
    with pytest.raises(RunLogError, match="no_new_rows but rows were written"):
        with runlog.attempt("demo") as attempt:
            attempt.record_landed([_part()], manifest_key=MANIFEST)
            attempt.mark_no_new_rows()

    assert [r["status"] for r in runlog.rows] == ["failed"]


# --- the log table itself failing -------------------------------------------


def test_a_failed_log_write_on_a_clean_exit_fails_the_run() -> None:
    """An observability table that goes quietly empty is ADR-010."""
    runlog = MemoryRunLog(fail_writes=True)
    with pytest.raises(RuntimeError, match="simulated log write failure"):
        with runlog.attempt("demo") as attempt:
            attempt.record_landed([_part()], manifest_key=MANIFEST)


def test_a_failed_log_write_never_masks_the_original_exception() -> None:
    """The traceback must name the real problem, not the log."""
    runlog = MemoryRunLog(fail_writes=True)
    with pytest.raises(ValueError, match="the real problem"):
        with runlog.attempt("demo"):
            raise ValueError("the real problem")


# --- DuckDB backend ---------------------------------------------------------


def test_duckdb_runlog_round_trip(tmp_path: Path) -> None:
    path = str(tmp_path / "state.duckdb")
    runlog = DuckDBRunLog(run_id="r1", triggered_by="test", path=path)

    with runlog.attempt("demo") as attempt:
        attempt.record_landed([_part(3)], manifest_key=MANIFEST)
        attempt.details["release_year"] = "2023"

    with duckdb.connect(path, read_only=True) as con:
        rows = con.execute(
            "SELECT run_id, source, status, rows_written, details " "FROM ingestion_log"
        ).fetchall()

    assert rows == [("r1", "demo", "success", 3, '{"release_year": "2023"}')]


def test_the_table_rejects_an_unknown_status(tmp_path: Path) -> None:
    path = str(tmp_path / "state.duckdb")
    DuckDBRunLog(run_id="r1", triggered_by="test", path=path)

    with duckdb.connect(path) as con:
        with pytest.raises(duckdb.ConstraintException):
            con.execute(
                "INSERT INTO ingestion_log (attempt_id, run_id, source, status, "
                "rows_read, rows_written, parts_written, bytes_written, "
                "started_at, finished_at, duration_ms, triggered_by, "
                "full_refresh, details) VALUES ('a', 'r', 's', 'nonsense', "
                "0, 0, 0, 0, now(), now(), 0, 'test', false, '{}')"
            )
