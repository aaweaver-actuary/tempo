"""Explicit Intelligence owner operation. Startup never calls migrate."""
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql


def migration_sources():
    directory = Path(__file__).parent / "migrations"
    seen = set()
    for entry in json.loads((directory / "manifest.json").read_text()):
        name = entry["path"]
        if "/" in name or ".." in name or not name.endswith(".sql") or entry["id"] in seen or not set(entry["depends_on"]) <= seen:
            raise ValueError("invalid_migration_manifest")
        content = (directory / name).read_bytes()
        yield entry["id"], hashlib.sha256(content).hexdigest(), content.decode()
        seen.add(entry["id"])


def expected_migrations():
    return {identity: checksum for identity, checksum, _ in migration_sources()}


def migrate(owner_url, workspace_id, runtime_role=None):
    if not workspace_id or len(workspace_id) > 200:
        raise ValueError("invalid_workspace")
    with psycopg.connect(owner_url) as database:
        database.execute("SELECT pg_advisory_xact_lock(hashtextextended('intelligence:migrations',0))")
        exists = database.execute("SELECT to_regclass('intelligence.migration_history')").fetchone()[0]
        applied = dict(database.execute("SELECT id,checksum FROM intelligence.migration_history").fetchall()) if exists else {}
        if set(applied) - set(expected_migrations()):
            raise ValueError("unknown_migration")
        for identity, checksum, content in migration_sources():
            if identity in applied:
                if applied[identity] != checksum:
                    raise ValueError("migration_checksum_changed")
            else:
                database.execute(content, prepare=False)
                database.execute("INSERT INTO intelligence.migration_history VALUES(%s,%s)",(identity,checksum))
        database.execute("INSERT INTO intelligence.instance VALUES(1,%s) ON CONFLICT DO NOTHING",(workspace_id,))
        if database.execute("SELECT workspace_id FROM intelligence.instance WHERE id=1").fetchone()[0] != workspace_id:
            raise ValueError("workspace_rebind_refused")
        database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        database.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA intelligence FROM PUBLIC")
    if runtime_role:
        grant_runtime(owner_url, runtime_role)


def grant_runtime(owner_url, runtime_role):
    with psycopg.connect(owner_url) as database:
        role = sql.Identifier(runtime_role)
        database.execute(sql.SQL("GRANT USAGE ON SCHEMA intelligence TO {}").format(role))
        database.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA intelligence TO {}").format(role))
        database.execute(sql.SQL("GRANT INSERT ON intelligence.requests,intelligence.evidence,intelligence.snapshots TO {}").format(role))
        database.execute(sql.SQL("GRANT INSERT ON intelligence.model_profiles TO {}").format(role))
        for table in ("request_heads","caches","commands","provider_control","credential_registrations","credential_head","maia_jobs"):
            database.execute(sql.SQL("GRANT INSERT,UPDATE ON intelligence.{} TO {}").format(sql.Identifier(table),role))
        # Runtime cannot rewrite accepted evidence, caller intent, or migration/workspace authority.
        database.execute(sql.SQL("REVOKE INSERT,UPDATE,DELETE ON intelligence.migration_history,intelligence.instance FROM {}").format(role))
