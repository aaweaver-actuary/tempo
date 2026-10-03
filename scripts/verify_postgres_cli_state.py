"""Fingerprint unchanged study history during a stopped-writer schema upgrade."""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from migrate_sqlite_to_postgres import destination_fingerprint
from verify_postgres_backup import table_layout


def study_fingerprints(database, expected=None):
    layout = table_layout(database)
    fingerprints = {}
    for table in ("reviews", "daily_queue", "operation_receipts"):
        primary_key, columns = layout[table]
        names = list(expected[table]["columns"]) if expected else [column[0] for column in columns]
        if table == "operation_receipts" and not expected:
            # Retry ownership columns intentionally change in migration 018.
            names = ["operation_id", "command_name", "request_hash", "state",
                     "response_json", "error_json", "created_at", "updated_at"]
        count, digest = destination_fingerprint(database, table, names, list(primary_key), set())
        fingerprints[table] = {"columns": names, "count": count, "digest": digest}
    return fingerprints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", help="Previously captured fingerprints, without row contents")
    args = parser.parse_args()
    expected = json.loads(args.expected) if args.expected else None
    with psycopg.connect(os.environ["TEMPO_POSTGRES_ADMIN_URL"],
                         options="-c default_transaction_read_only=on") as database:
        actual = study_fingerprints(database, expected)
    if expected and actual != expected:
        raise RuntimeError("Schema upgrade changed review, queue, or operation receipt history; keep writers stopped")
    print(json.dumps(actual))


if __name__ == "__main__":
    main()
