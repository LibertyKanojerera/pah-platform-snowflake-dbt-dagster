"""One entry point for every source.

Thin by design: argument parsing and resource wiring only. Anything that
decides what the data means belongs in the source modules, where it is tested.

Ordering matters for `all`. CMS first, because the NPI set NPPES enriches is
derived from landed CMS data; Postgres last, because it is independent and
should not block the other two.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ingest.core.cache import JsonCache
from ingest.core.config import get_settings
from ingest.core.http import HttpClient
from ingest.core.landing import LandingWriter
from ingest.core.log import configure_logging, get_logger
from ingest.core.state import DuckDBStateStore, StateStore
from ingest.sources import app_postgres, cms_part_d, nppes

log = get_logger(__name__)


def _writer() -> LandingWriter:
    bucket = get_settings().landing_bucket
    if not bucket:
        raise SystemExit("LANDING_BUCKET is not set; see .env.example")
    return LandingWriter(bucket=bucket)


def run_cms(args: argparse.Namespace, writer: LandingWriter, store: StateStore) -> int:
    vocabulary = cms_part_d.DrugVocabulary.from_csv()

    with HttpClient(source="cms_part_d", base_url=cms_part_d.CMS_BASE_URL) as client:
        if args.year:
            dataset = cms_part_d.resolve_dataset(
                cms_part_d.discover_datasets(client), title_contains=args.year
            )
            if args.dry_run:
                log.info("cms.would_land", year=args.year, uuid=dataset.uuid)
                return 0
            _batch, part, _ = cms_part_d.land_release(
                client, dataset, vocabulary, writer, dt=args.dt
            )
            return part.row_count

        landed = cms_part_d.land_all_releases(
            client,
            vocabulary,
            writer,
            store,
            dt=args.dt,
            full_refresh=args.full_refresh,
            dry_run=args.dry_run,
        )
        return sum(v for v in landed.values() if v > 0)


def run_nppes(
    args: argparse.Namespace, writer: LandingWriter, store: StateStore
) -> int:
    npis = nppes.distinct_npis_from_landing(get_settings().landing_bucket)
    cache = None if args.no_cache else JsonCache(namespace="nppes")

    log.info("nppes.plan", npis=len(npis), cached=cache is not None)
    if args.dry_run:
        return 0

    with HttpClient(source="nppes", base_url=nppes.NPPES_BASE_URL) as client:
        _batch, part, _ = nppes.land_npis(client, npis, writer, dt=args.dt, cache=cache)
    return part.row_count


def run_postgres(
    args: argparse.Namespace, writer: LandingWriter, store: StateStore
) -> int:
    if args.dry_run:
        for table in app_postgres.TABLES:
            checkpoint = store.read(table.source_id)
            log.info(
                "postgres.would_extract",
                table=table.name,
                watermark=checkpoint.watermark if checkpoint else None,
            )
        return 0

    results = app_postgres.extract_all(
        writer, store, dt=args.dt, full_refresh=args.full_refresh
    )
    return sum(results.values())


COMMANDS = {
    "cms": run_cms,
    "nppes": run_nppes,
    "postgres": run_postgres,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pah-ingest", description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="force JSON output; the default follows whether stderr is a TTY",
    )
    parser.add_argument(
        "--dt",
        type=date.fromisoformat,
        default=None,
        help="landing partition date, defaults to today (UTC)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument(
            "--full-refresh",
            action="store_true",
            help="ignore the checkpoint and re-read everything",
        )

    cms = subparsers.add_parser("cms", help="CMS Part D prescribers")
    add_common(cms)
    cms.add_argument(
        "--year",
        help="land one release by title fragment, e.g. 2023-12-31; "
        "omit to backfill every release newer than the checkpoint",
    )

    nppes_parser = subparsers.add_parser("nppes", help="NPPES enrichment")
    add_common(nppes_parser)
    nppes_parser.add_argument(
        "--no-cache", action="store_true", help="bypass the cache entirely"
    )

    pg = subparsers.add_parser("postgres", help="application Postgres source")
    add_common(pg)

    all_parser = subparsers.add_parser("all", help="cms, then nppes, then postgres")
    add_common(all_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level=args.log_level, force_json=args.json_logs or None)

    writer = _writer()
    store = DuckDBStateStore()

    if args.command == "all":
        args.year = None
        args.no_cache = False
        total = 0
        for name in ("cms", "nppes", "postgres"):
            log.info("cli.source_start", source=name)
            total += COMMANDS[name](args, writer, store)
        log.info("cli.complete", rows=total)
        return 0

    rows = COMMANDS[args.command](args, writer, store)
    log.info("cli.complete", source=args.command, rows=rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
