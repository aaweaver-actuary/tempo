"""Fingerprint unchanged study history during a stopped-writer schema upgrade."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))

import argparse
import json
import os

import psycopg

from database.tooling.migrate_sqlite_to_postgres import destination_fingerprint
from database.tooling.verify_postgres_backup import table_layout


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


# Preliminary cutover evidence contract. This does not replace study_fingerprints
# or authorize a migration: the delivery owner must first approve the cohort and
# connect this contract to a stopped-writer, pre-cut PostgreSQL snapshot.
THREE_JOURNEY_EVIDENCE_VERSION = 1


def propose_three_journey_card_deletes(cards, retired_references, retained_references, origins):
    """Classify only exact defense ownership; preserve conflicting/unknown evidence.

    References are explicit card IDs supplied by the respective owners. Original
    issued content type is an independent witness and cannot be overwritten by a
    later card edit. A reference to an absent card is reported, not discarded.
    """
    by_id = {card['id']: card for card in cards}
    if len(by_id) != len(cards):
        raise ValueError('duplicate card identity')
    retired = set(retired_references)
    retained = set(retained_references)
    original_types = {}
    for origin in origins:
        original_types.setdefault(origin['card_id'], set()).add(origin['content_type'])
    proposals, ambiguous = [], {}
    for card_id, card in by_id.items():
        types = original_types.get(card_id, set())
        reasons = []
        if card_id in retained:
            reasons.append('retained reference')
        if types - {'defense'}:
            reasons.append('non-defense or unknown issued origin')
        if card['content_type'] != 'defense':
            if card_id in retired or 'defense' in types:
                reasons.append('conflicting current card type')
        elif reasons:
            pass
        else:
            proposals.append(card_id)
        if reasons:
            ambiguous[card_id] = sorted(reasons)
    for card_id in (retired | retained | set(original_types)) - set(by_id):
        ambiguous[card_id] = ['reference to missing card']
    return {'version': THREE_JOURNEY_EVIDENCE_VERSION,
            'proposed_card_ids': sorted(proposals), 'ambiguous': ambiguous}


def three_journey_expected_rows(source_rows, primary_keys, proposed_keys):
    """Build a pre-cut witness from exact composite keys and complete row content.

    The caller supplies independently reviewed deletion keys for each table;
    unmentioned tables and rows are retained. No payload/name matching is used.
    This JSON-compatible draft includes synthetic row contents and is not suitable
    for logging real user data. Production evidence must use protected digests.
    """
    result = {}
    for table, rows in source_rows.items():
        columns = primary_keys[table]
        indexed = {}
        for row in rows:
            key = tuple(row[column] for column in columns)
            if key in indexed:
                raise ValueError(f'duplicate key in {table}')
            indexed[key] = row
        deletes = {tuple(key) for key in proposed_keys.get(table, [])}
        if not deletes <= indexed.keys():
            raise ValueError(f'unknown proposed key in {table}')
        result[table] = {'primary_key': list(columns),
                         'retained': [indexed[key] for key in sorted(indexed.keys() - deletes)],
                         'deleted_keys': [list(key) for key in sorted(deletes)]}
    if set(proposed_keys) - set(source_rows):
        raise ValueError('proposed deletion for unknown table')
    return {'version': THREE_JOURNEY_EVIDENCE_VERSION, 'tables': result}


def verify_three_journey_rows(expected, actual_rows):
    """Reject changed, missing, newly added, or supposedly deleted rows."""
    if expected.get('version') != THREE_JOURNEY_EVIDENCE_VERSION:
        raise ValueError('unsupported evidence version')
    if set(expected['tables']) != set(actual_rows):
        raise ValueError('table coverage changed')
    actual = three_journey_expected_rows(actual_rows,
        {name: item['primary_key'] for name, item in expected['tables'].items()}, {})
    for name, item in expected['tables'].items():
        if actual['tables'][name]['retained'] != item['retained']:
            raise ValueError(f'retained or deleted rows differ in {name}')


def three_journey_digest_witness(source_rows, primary_keys, proposed_keys, secret):
    """Produce keyed source-derived witnesses without disclosing keys or contents.

    Retain the same secret and original witness across the cut and all retries.
    This draft does not select deletion keys or connect itself to the CLI guard.
    """
    import hashlib
    import hmac

    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError('witness secret must contain at least 32 bytes')
    if set(source_rows) != set(primary_keys) or set(proposed_keys) - set(source_rows):
        raise ValueError('table coverage differs')

    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, default=str).encode('utf-8')

    def digest(domain, value):
        return hmac.new(secret, domain.encode() + b'\x00' + encode(value), hashlib.sha256).hexdigest()

    result = {}
    for table, rows in source_rows.items():
        indexed = {}
        for row in rows:
            key = tuple(row[column] for column in primary_keys[table])
            if key in indexed:
                raise ValueError(f'duplicate key in {table}')
            indexed[key] = row
        deleted = {tuple(key) for key in proposed_keys.get(table, [])}
        if not deleted <= indexed.keys():
            raise ValueError(f'unknown proposed key in {table}')
        retained = indexed.keys() - deleted
        def fingerprint(keys):
            entries = sorted((digest(table + ':key', key), digest(table + ':row', indexed[key]))
                             for key in keys)
            return {'count': len(entries), 'digest': digest(table + ':set', entries)}
        result[table] = {'retained': fingerprint(retained), 'deleted': fingerprint(deleted),
                         'source': fingerprint(indexed.keys())}
    return {'version': THREE_JOURNEY_EVIDENCE_VERSION, 'tables': result}


def verify_three_journey_digest(expected, actual_rows, primary_keys, secret):
    if expected.get('version') != THREE_JOURNEY_EVIDENCE_VERSION:
        raise ValueError('unsupported evidence version')
    if set(expected['tables']) != set(actual_rows):
        raise ValueError('table coverage changed')
    actual = three_journey_digest_witness(actual_rows, primary_keys, {}, secret)
    for table in expected['tables']:
        if actual['tables'][table]['source'] != expected['tables'][table]['retained']:
            raise ValueError(f'retained or deleted rows differ in {table}')
