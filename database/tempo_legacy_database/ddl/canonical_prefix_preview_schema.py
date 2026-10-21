"""Existing SQLite compatibility prefix-preview DDL and initialization."""

TABLE_DEFINITIONS = (
    """CREATE TABLE IF NOT EXISTS canonical_prefix_previews(
        id TEXT PRIMARY KEY,repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
        moves_json TEXT NOT NULL,expected_revision BIGINT NOT NULL,source_revision BIGINT NOT NULL,
        state TEXT NOT NULL DEFAULT 'checking',suggestion_json TEXT NOT NULL DEFAULT '[]',
        last_error TEXT,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS canonical_prefix_results(
        preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,
        disagreement_ply BIGINT,origin_json TEXT,scope_start_ply BIGINT,
        PRIMARY KEY(preview_id,item_id))""",
    """CREATE TABLE IF NOT EXISTS canonical_prefix_positions(
        preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
        fen_key TEXT NOT NULL,in_scope BIGINT NOT NULL,fen TEXT NOT NULL,route_json TEXT NOT NULL,
        ply BIGINT NOT NULL,source_revision BIGINT NOT NULL DEFAULT -1,PRIMARY KEY(preview_id,fen_key,in_scope))""",
)


def initialize_sqlite_schema(database) -> None:
    for definition in TABLE_DEFINITIONS:
        database.execute(definition)
    if 'source_revision' not in {row[1] for row in database.execute('PRAGMA table_info(canonical_prefix_positions)')}:
        database.execute('ALTER TABLE canonical_prefix_positions ADD COLUMN source_revision BIGINT NOT NULL DEFAULT -1')
    database.execute('CREATE INDEX IF NOT EXISTS canonical_prefix_preview_versions ON canonical_prefix_previews(repertoire_id,expected_revision,source_revision,created_at DESC)')
    from tempo_legacy_database.ddl.canonical_scope_schema import initialize_scope_schema
    initialize_scope_schema(database)
