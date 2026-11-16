"""Reconcile ingestion_log against the landed manifests.

Every committed batch must have a success row, every success row a committed
batch, and the counts must agree. Exits 1 on any anomaly, so it can gate.
Reads the production root only; landing_test/ is out of scope by design.
"""

from __future__ import annotations

import sys

from ingest.core.config import get_settings
from ingest.core.duck import s3_connection
from ingest.core.state import DEFAULT_DUCKDB_PATH

SQL = """
WITH manifests AS (
    SELECT
        json_extract_string(content, '$.source')                   AS source,
        json_extract_string(content, '$.batch_id')                 AS batch_id,
        CAST(json_extract(content, '$.row_count_total') AS BIGINT) AS row_count_total
    FROM read_text('s3://{bucket}/landing/*/*/*/_manifest.json')
),
logged AS (
    SELECT source, batch_id, rows_read, rows_written
    FROM meta.ingestion_log
    WHERE status = 'success'
),
compared AS (
    SELECT
        coalesce(m.source, l.source)     AS source,
        coalesce(m.batch_id, l.batch_id) AS batch_id,
        CASE
            WHEN l.batch_id IS NULL                  THEN 'landed_not_logged'
            WHEN m.batch_id IS NULL                  THEN 'logged_not_landed'
            WHEN l.rows_written <> m.row_count_total THEN 'written_vs_manifest'
            WHEN l.rows_read <> l.rows_written       THEN 'read_vs_written'
        END AS anomaly
    FROM manifests AS m
    FULL OUTER JOIN logged AS l ON m.batch_id = l.batch_id
)
SELECT source, anomaly, count(*) AS batches
FROM compared
WHERE anomaly IS NOT NULL
GROUP BY ALL
ORDER BY source, anomaly
"""


def main() -> int:
    with s3_connection() as con:
        con.execute(f"ATTACH '{DEFAULT_DUCKDB_PATH}' AS meta (READ_ONLY)")
        rows = con.execute(SQL.format(bucket=get_settings().landing_bucket)).fetchall()

    if not rows:
        print("reconciliation: zero anomalies")
        return 0
    for source, anomaly, batches in rows:
        print(f"{source:32} {anomaly:22} {batches}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
