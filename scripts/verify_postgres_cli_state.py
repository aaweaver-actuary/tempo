"""Fingerprint unchanged study history during a stopped-writer schema upgrade."""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from migrate_sqlite_to_postgres import destination_fingerprint
from verify_postgres_backup import table_layout


# Protect membership, ordering, learner results, and admission provenance.
# Queue bucket/priority labels are derived metadata: migration 025 normalizes
# the legacy game-tactics bucket. Receipt retry/lease ownership is deliberately
# transformed by recovery migrations (notably 018). New physical columns do not
# automatically become immutable history merely because they exist.
STUDY_HISTORY_COLUMNS = {
    "reviews": ("id", "card_id", "rating", "reviewed_at", "previous_interval", "next_interval",
                "internal_rating", "guided", "source_kind", "source_ref", "invalidated_at", "invalidation_reason"),
    "daily_queue": ("id", "queue_date", "card_id", "cycle", "position", "status", "attempt_state",
                    "review_result_json", "attempt_failed", "admission_kind", "admission_repertoire_id", "admission_source"),
    "operation_receipts": ("operation_id", "command_name", "request_hash", "state",
                           "response_json", "error_json", "created_at", "updated_at"),
}


def study_fingerprints(database, expected=None):
    layout = table_layout(database)
    fingerprints = {}
    for table, historical_columns in STUDY_HISTORY_COLUMNS.items():
        primary_key, columns = layout[table]
        names = list(historical_columns)
        if not set(names).issubset(column[0] for column in columns):
            raise RuntimeError(f"Required historical columns are missing from {table}")
        if expected and expected[table]["columns"] != names:
            raise RuntimeError(f"Historical fingerprint contract differs for {table}")
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
