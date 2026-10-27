"""PREPARED native source/publication boundary; qualification is still pending.

Uses #483's actual source reservation and existing schema fixture, plus #501's
real publication SQL. Other Analysis generations remain a private test owner:
this file cannot establish production settings/personal/evidence freshness.
Run only after the prioritized Study and browser checks release validation.
"""
from contextlib import contextmanager
import json
from uuid import uuid4

import chess
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app.product_composition.analysis_publications import CurrentRepertoireAnalysisFence
from tempo_legacy_database.adapters.analysis.publications import PostgresAnalysisPublications, SCHEMA_SQL
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import (
    SnapshotReservationUnavailable, current_snapshot,
)
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from test_product_analysis_source_fence import prepared_for, source_fence
from test_product_introductions_postgres import owned_postgres
from test_product_snapshot_postgres import DDL, prepare_reservation_contract


@pytest.fixture
def source_publication(owned_postgres):
    schema = "analysis_source_" + uuid4().hex
    with psycopg.connect(**owned_postgres, row_factory=dict_row) as raw:
        raw.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        raw.execute(DDL, prepare=False)
        raw.execute(SCHEMA_SQL, prepare=False)
        raw.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
        raw.execute("INSERT INTO repertoires VALUES('rep','Synthetic',7,0,'[]')")
        raw.execute("INSERT INTO opening_graph_publications VALUES('rep',3,'ready')")
        moves = json.dumps(["e2e4", "e7e5", "g1f3"])
        raw.execute("INSERT INTO repertoire_lines VALUES('route','rep','Route','white',%s,%s)",
                    (chess.STARTING_FEN, moves))
        raw.execute("INSERT INTO cards VALUES('physical','rep',1,%s,%s,'white','opening',0,0,NULL,1,NULL)",
                    (chess.STARTING_FEN, moves))
        raw.execute("INSERT INTO repertoire_cards VALUES('rep','physical',1)")
        raw.execute("INSERT INTO opening_graph_steps VALUES('rep',3,'route',0,'physical',%s,%s,'white')",
                    (chess.STARTING_FEN, moves))
        prepare_reservation_contract(raw)
    hooks = {"inside": None, "fail_commit": False}

    @contextmanager
    def transaction():
        with psycopg.connect(**owned_postgres, row_factory=tempo_row_factory) as raw:
            raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            raw.execute("SET LOCAL lock_timeout='50ms'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)
            if hooks["fail_commit"]:
                raise RuntimeError("synthetic_publication_commit_failed")

    @contextmanager
    def peer():
        with psycopg.connect(**owned_postgres, row_factory=dict_row) as raw:
            raw.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            raw.execute("SET LOCAL lock_timeout='50ms'")
            raw.execute("SET LOCAL statement_timeout='1s'")
            yield raw

    def test_generations(database, snapshot):
        if hooks["inside"] is not None:
            hooks["inside"]()
        # Explicit synthetic immutable Analysis generation fixture. Source facts
        # come only from the actual reserved snapshot; no production owner claim.
        return source_fence(snapshot)

    adapter = PostgresAnalysisPublications(transaction,
        current_fence=CurrentRepertoireAnalysisFence(test_generations))
    try:
        with transaction() as database:
            original = prepared_for(current_snapshot(database, "rep"))
        yield adapter, transaction, peer, hooks, original
    finally:
        with psycopg.connect(**owned_postgres) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_native_source_is_held_through_publication_then_changed_introduction_obsoletes_it(source_publication):
    adapter, _, peer, hooks, original = source_publication
    def attempt_foreground_change():
        with peer() as writer:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.execute("UPDATE cards SET introduced_at='2026-10-27T00:00:00Z' WHERE id='physical'")
            writer.rollback()
    hooks["inside"] = attempt_foreground_change
    assert adapter.publish_if_current(original)
    hooks["inside"] = None
    with peer() as writer:
        writer.execute("UPDATE cards SET introduced_at='2026-10-27T00:00:00Z' WHERE id='physical'")
    assert not adapter.publish_if_current(original)
    assert adapter.current_result("rep") is None and adapter.latest_result("rep") == original


def test_native_busy_source_preserves_publication_and_releases_partial_reservations(source_publication):
    adapter, _, peer, _, original = source_publication
    assert adapter.publish_if_current(original)
    with peer() as foreground:
        foreground.execute("SELECT id FROM cards WHERE id='physical' FOR UPDATE")
        with pytest.raises(SnapshotReservationUnavailable, match="snapshot_source_busy"):
            adapter.publish_if_current(original)
    assert adapter.latest_result("rep") == original and adapter.publish_if_current(original)


def test_native_failed_publication_commit_releases_source_without_installing_result(source_publication):
    adapter, _, peer, hooks, original = source_publication
    hooks["fail_commit"] = True
    with pytest.raises(RuntimeError, match="synthetic_publication_commit_failed"):
        adapter.publish_if_current(original)
    hooks["fail_commit"] = False
    assert adapter.latest_result("rep") is None
    with peer() as writer:
        writer.execute("UPDATE cards SET introduced_at='2026-10-27T00:00:00Z' WHERE id='physical'")
    assert not adapter.publish_if_current(original)
