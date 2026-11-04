"""Preliminary, unbound observations of Tactics-owned PostgreSQL rows.

The caller owns the read-only repeatable-read transaction. This does not bind
assets, Study references, browser journals, a final watermark, or a writer fence.
"""
from dataclasses import dataclass, make_dataclass
from datetime import datetime
from typing import Any, Protocol

from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


class NativeTacticsReadScope(Protocol):
    in_transaction: bool
    read_only: bool
    def execute_native(self, statement: str, parameters: tuple = ()) -> Any: ...


# Explicit schema and order: a newly added column must not disappear silently.
_TABLES = (
    ('tactic_progress', (('puzzle_id', 'text'), ('deck_id', 'text'), ('card_id', 'text'), ('clean_pass_at', 'text'), ('admitted_at', 'text'), ('admission_mode', 'text'))),
    ('tactic_pack_activation', (('pack_id', 'text'), ('active', 'bigint'))),
    ('tactic_rotation', (('id', 'bigint'), ('last_pack_id', 'text'))),
    ('tactic_introductions', (('puzzle_id', 'text'), ('pack_id', 'text'), ('introduction_date', 'text'))),
    ('tactic_discovery_attempts', (('id', 'text'), ('deck_id', 'text'), ('puzzle_id', 'text'), ('clean', 'bigint'), ('result_json', 'text'))),
    ('tactic_captures', (('id', 'text'), ('card_id', 'text'), ('source_kind', 'text'), ('source_ref', 'text'), ('source_url', 'text'), ('note', 'text'), ('captured_at', 'text'), ('request_json', 'text'), ('result_json', 'text'))),
)
SCOPE_TABLES = tuple(name for name, _ in _TABLES)

# Fields preserve stored values; nullable fields are not reconstructed.
ProgressRow = make_dataclass('ProgressRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[0][1]], frozen=True)
PackActivationRow = make_dataclass('PackActivationRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[1][1]], frozen=True)
RotationRow = make_dataclass('RotationRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[2][1]], frozen=True)
IntroductionRow = make_dataclass('IntroductionRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[3][1]], frozen=True)
DiscoveryAttemptRow = make_dataclass('DiscoveryAttemptRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[4][1]], frozen=True)
CaptureRow = make_dataclass('CaptureRow', [(n, int if t == 'bigint' else str | None) for n, t in _TABLES[5][1]], frozen=True)
_ROW_TYPES = (ProgressRow, PackActivationRow, RotationRow, IntroductionRow, DiscoveryAttemptRow, CaptureRow)


@dataclass(frozen=True)
class TacticsReadBlocked:
    status: str
    code: str


@dataclass(frozen=True)
class SnapshotProvenance:
    database: str
    schema: str
    schema_version: int
    snapshot: str
    backend_pid: int
    transaction_started_at: datetime


@dataclass(frozen=True)
class PreliminaryTacticsRows:
    provenance: SnapshotProvenance
    tactic_progress: tuple[ProgressRow, ...]
    tactic_pack_activation: tuple[PackActivationRow, ...]
    tactic_rotation: tuple[RotationRow, ...]
    tactic_introductions: tuple[IntroductionRow, ...]
    tactic_discovery_attempts: tuple[DiscoveryAttemptRow, ...]
    tactic_captures: tuple[CaptureRow, ...]
    status: str = 'PRELIMINARY_UNBOUND'
    asset_authority: str = 'UNPROVEN'
    writer_fence: None = None
    final_watermark: None = None
    browser_authority: str = 'UNRECONCILED'
    scope_tables: tuple[str, ...] = SCOPE_TABLES


def _block(code):
    return TacticsReadBlocked('BLOCK', code)


def read_tactics_row_snapshot(tx: NativeTacticsReadScope, *, max_rows: int, max_text_bytes: int) -> PreliminaryTacticsRows | TacticsReadBlocked:
    if type(max_rows) is not int or max_rows <= 0 or type(max_text_bytes) is not int or max_text_bytes <= 0:
        raise ValueError('positive integer limits required')
    if not tx.in_transaction or not tx.read_only:
        return _block('ACTIVE_READ_ONLY_TRANSACTION_REQUIRED')
    scope = tx.execute_native("""SELECT current_database() AS db, current_schema() AS schema,
        current_setting('transaction_isolation') AS isolation, current_setting('transaction_read_only') AS ro,
        pg_current_snapshot()::text AS snapshot, pg_backend_pid() AS pid, transaction_timestamp() AS started""").fetchone()
    if scope['isolation'] != 'repeatable read' or scope['ro'] != 'on' or scope['schema'] != 'public':
        return _block('READ_ONLY_REPEATABLE_READ_PUBLIC_REQUIRED')
    # Catalog inspection also avoids querying absent relations and aborting the transaction.
    schema = tx.execute_native("""SELECT c.relname AS table_name, a.attname AS column_name,
        pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname = ANY(%s)
          AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""", (list(SCOPE_TABLES) + ['tempo_schema_migrations'],)).fetchall()
    columns = {}
    for row in schema:
        columns.setdefault(row['table_name'], []).append((row['column_name'], row['data_type']))
    if any(tuple(columns.get(name, ())) != fields for name, fields in _TABLES) or not any(
            name == 'version' for name, _ in columns.get('tempo_schema_migrations', ())):
        return _block('SCHEMA_UNSUPPORTED')
    version = tx.execute_native('SELECT max(version) AS version FROM public.tempo_schema_migrations').fetchone()['version']
    if version != POSTGRES_SCHEMA_VERSION:
        return _block('SCHEMA_UNSUPPORTED')
    keys = tx.execute_native("""SELECT c.relname AS table_name, a.attname AS column_name
        FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE n.nspname='public' AND i.indisprimary AND c.relname = ANY(%s)
        ORDER BY c.relname,k.ord""", (list(SCOPE_TABLES),)).fetchall()
    found_keys = {}
    for key in keys:
        found_keys.setdefault(key['table_name'], []).append(key['column_name'])
    if any(found_keys.get(name) != [fields[0][0]] for name, fields in _TABLES):
        return _block('SCHEMA_UNSUPPORTED')
    provenance = SnapshotProvenance(scope['db'], scope['schema'], version, scope['snapshot'], scope['pid'], scope['started'])
    remaining, text_remaining = max_rows, max_text_bytes
    result = []
    for (table, fields), row_type in zip(_TABLES, _ROW_TYPES):
        names = ','.join(name for name, _ in fields)
        pk, pk_type = fields[0]
        order = pk + (' COLLATE "C"' if pk_type == 'text' else '')
        lengths = ' + '.join('coalesce(octet_length(' + name + '),0)' for name, kind in fields if kind == 'text') or '0'
        # Only lengths are returned before the bounded values are fetched.
        stats = tx.execute_native(f'SELECT count(*) AS n, coalesce(sum(bytes),0) AS bytes FROM (SELECT {lengths} AS bytes FROM public.{table} ORDER BY {order} LIMIT %s) bounded', (remaining + 1,)).fetchone()
        if stats['n'] > remaining:
            return _block('ROW_LIMIT_EXCEEDED')
        if stats['bytes'] > text_remaining:
            return _block('TEXT_LIMIT_EXCEEDED')
        rows = tx.execute_native(f'SELECT {names} FROM public.{table} ORDER BY {order} LIMIT %s', (remaining + 1,)).fetchall()
        result.append(tuple(row_type(*(row[name] for name, _ in fields)) for row in rows))
        remaining -= stats['n']
        text_remaining -= stats['bytes']
    return PreliminaryTacticsRows(provenance, *result)
