"""Landing zone writer: Parquet to S3 under a partitioned, batch-scoped key.

Two decisions carry the weight here.

**The manifest is a commit marker.** Data parts are uploaded first and
`_manifest.json` is written last, so a prefix carrying a manifest is complete
by construction. An extractor that dies mid-upload leaves a prefix with no
manifest, and downstream skips it. This is the same role `_SUCCESS` plays in
Hadoop-lineage lakes, and it is why a crashed run cannot be mistaken for a
short one.

**A batch never overwrites.** Every run gets a fresh UUID prefix, so a re-run
produces a second prefix rather than replacing the first. That is what makes
the idempotency test meaningful: two runs, two prefixes, one reconciled row
count after the merge. It also means a bad batch can be excluded without
touching the batches around it.

Key layout, matching docs/architecture.md:

    landing/<source>/dt=YYYY-MM-DD/batch=<uuid>/part-0000.parquet
                                               /_manifest.json
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from ingest.core.log import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = get_logger(__name__)

#: Set at landing, never in dbt, so staging can be rebuilt from raw without
#: losing lineage. Excluded from the row hash: a row's identity is its source
#: content, not the run that happened to carry it.
PROVENANCE_COLUMNS = (
    "_source",
    "_batch_id",
    "_file_name",
    "_ingested_at",
    "_source_row_hash",
)

MANIFEST_NAME = "_manifest.json"
WRITER_VERSION = 1


@dataclass(frozen=True)
class Batch:
    """One extractor run."""

    source: str
    batch_id: str
    dt: date
    ingested_at: datetime

    @classmethod
    def new(cls, source: str, *, dt: date | None = None) -> Batch:
        now = datetime.now(timezone.utc)
        return cls(
            source=source,
            batch_id=str(uuid.uuid4()),
            dt=dt or now.date(),
            ingested_at=now,
        )

    @property
    def prefix(self) -> str:
        return f"landing/{self.source}/dt={self.dt.isoformat()}/batch={self.batch_id}"

    def key(self, filename: str) -> str:
        return f"{self.prefix}/{filename}"


@dataclass(frozen=True)
class LandedPart:
    key: str
    row_count: int
    byte_count: int
    sha256: str


def row_hash(row: Mapping[str, Any]) -> str:
    """Stable hash of the source fields.

    Canonical JSON with sorted keys, so the same logical row hashes the same
    across runs regardless of dict ordering. `default=str` keeps dates and
    Decimals hashable rather than raising.
    """
    payload = {k: v for k, v in row.items() if k not in PROVENANCE_COLUMNS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def add_provenance(
    rows: Sequence[Mapping[str, Any]], *, batch: Batch, object_key: str
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        out["_source"] = batch.source
        out["_batch_id"] = batch.batch_id
        out["_file_name"] = object_key  # the full key, not the bare part name
        out["_ingested_at"] = batch.ingested_at
        out["_source_row_hash"] = row_hash(row)
        enriched.append(out)
    return enriched


class LandingWriter:
    """Writes Parquet parts and a closing manifest to one S3 bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        s3_client: "S3Client | None" = None,
        compression: str = "zstd",
    ) -> None:
        self.bucket = bucket
        self.compression = compression
        self._s3: S3Client = s3_client or boto3.client("s3")
        self._log = log.bind(bucket=bucket)

    def write_part(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        batch: Batch,
        part_number: int = 0,
    ) -> LandedPart:
        if not rows:
            raise ValueError("refusing to write an empty part")

        file_name = f"part-{part_number:04d}.parquet"
        key = batch.key(file_name)

        table = pa.Table.from_pylist(add_provenance(rows, batch=batch, object_key=key))

        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression=self.compression)
        body = buffer.getvalue()

        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body)

        part = LandedPart(
            key=key,
            row_count=table.num_rows,
            byte_count=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )

        self._log.info(
            "landing.part_written",
            key=key,
            rows=part.row_count,
            bytes=part.byte_count,
        )
        return part

    def write_manifest(
        self,
        *,
        batch: Batch,
        parts: Sequence[LandedPart],
        source_query: Mapping[str, Any] | None = None,
    ) -> str:
        """Close the batch. Call this only after every part has uploaded."""
        manifest = {
            "writer_version": WRITER_VERSION,
            "source": batch.source,
            "batch_id": batch.batch_id,
            "dt": batch.dt.isoformat(),
            "ingested_at": batch.ingested_at.isoformat(),
            "row_count_total": sum(p.row_count for p in parts),
            "byte_count_total": sum(p.byte_count for p in parts),
            "parts": [
                {
                    "key": p.key,
                    "row_count": p.row_count,
                    "byte_count": p.byte_count,
                    "sha256": p.sha256,
                }
                for p in parts
            ],
            "source_query": dict(source_query or {}),
        }

        key = batch.key(MANIFEST_NAME)
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        self._log.info(
            "landing.batch_committed",
            key=key,
            parts=len(parts),
            rows=manifest["row_count_total"],
        )
        return key
