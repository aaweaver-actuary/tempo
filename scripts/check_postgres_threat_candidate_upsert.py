"""Exercise the production defensive candidate upsert against PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
from apply_postgres_migrations import apply_migrations
from app import database, postgres_store
from app.postgres_store import postgres_sql
from app.services.threat_pipeline import THREAT_CANDIDATE_UPSERT_SQL
from issue12_persistence_scenario import verify_three_anchor_persistence


def verify_issue12_three_anchors_on_postgres(database_url: str) -> None:
    """Use migrated tables in a private database, never the runner's active product data."""
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Issue 12 persistence proof requires the disposable test runner")
    database_name = "tempo_issue12_" + uuid.uuid4().hex
    administrator_url = make_conninfo(database_url, dbname="postgres")
    scenario_url = make_conninfo(database_url, dbname=database_name)
    with psycopg.connect(administrator_url, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        apply_migrations(scenario_url)
        os.environ["TEMPO_DATABASE_WRITE_URL"] = scenario_url
        os.environ["TEMPO_DATABASE_READ_URL"] = scenario_url
        os.environ.pop("TEMPO_REDIS_URL", None)
        os.environ.pop("TEMPO_FOREGROUND_ACTIVITY_URL", None)
        with database.connection() as connection:
            connection.execute("INSERT INTO settings(id) VALUES(1)")
        verify_three_anchor_persistence(reconnect=postgres_store.close_pools)
        print("PASS issue12_three_anchors_persist_one_incident_and_replay_without_recurrence_inflation_postgres")
    finally:
        postgres_store.close_pools()
        with psycopg.connect(administrator_url, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


def main() -> None:
    database_url = os.getenv(
        "TEMPO_DATABASE_WRITE_URL", "postgresql://postgres@postgres:5432/tempo"
    )
    statement = postgres_sql(THREAT_CANDIDATE_UPSERT_SQL)
    assert statement is not None
    with psycopg.connect(database_url) as database:
        database.execute(
            """CREATE TEMP TABLE threat_training_candidates (
                   id TEXT PRIMARY KEY, finding_id TEXT, game_id TEXT,
                   analysis_version BIGINT, incident_id TEXT, player_ply BIGINT,
                   evidence_json TEXT, source_fingerprint TEXT, detector_version BIGINT,
                   policy_json TEXT, created_at TEXT, updated_at TEXT,
                   superseded_at TEXT, validation_state TEXT DEFAULT 'needs_analysis',
                   validation_json TEXT DEFAULT '{}', dismissed_at TEXT,
                   dismissed_evidence_fingerprint TEXT, approved_at TEXT,
                   exercise_revision BIGINT DEFAULT 1)"""
        )

        def upsert(fingerprint: str) -> None:
            database.execute(
                statement,
                ("candidate-one", "finding-one", "game-one", 1, "incident-one", 2,
                 "{}", fingerprint, 1, "{}", "now", "now"),
            )

        upsert("source-one")
        database.execute(
            """UPDATE threat_training_candidates SET validation_state='engine_supported',
                   validation_json='{"retained":true}', dismissed_at='dismissed',
                   dismissed_evidence_fingerprint='source-one', approved_at='approved',
                   exercise_revision=3 WHERE id='candidate-one'"""
        )
        upsert("source-one")
        unchanged = database.execute(
            "SELECT validation_state,validation_json,exercise_revision "
            "FROM threat_training_candidates WHERE id='candidate-one'"
        ).fetchone()
        assert unchanged == ("engine_supported", '{"retained":true}', 3), unchanged

        upsert("source-two")
        changed = database.execute(
            """SELECT validation_state,validation_json,dismissed_at,
                      dismissed_evidence_fingerprint,approved_at,exercise_revision
               FROM threat_training_candidates WHERE id='candidate-one'"""
        ).fetchone()
        assert changed == ("needs_analysis", "{}", None, None, None, 4), changed
    print("PASS PostgreSQL defensive candidate upsert replays and resets changed evidence")
    verify_issue12_three_anchors_on_postgres(database_url)


if __name__ == "__main__":
    main()
