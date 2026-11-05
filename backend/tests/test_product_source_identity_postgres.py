"""Canonical publication + feedback rollback/replay on task-owned PostgreSQL.

Uses the existing labelled fixture only. This is not provider HTTP, Study
admission, ordered-migration/role or installation acceptance.
"""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
import pytest

from app.games.providers.lichess_acquisition import LichessGamePage
from app.games.feedback.coordinator import Coordinator
from app.games.feedback.jobs import FeedbackRunner
from app.product_composition.game_sync import WorkflowComparer, WorkflowNormalizer
from app.product_composition.retained_game import retained_canonical_game
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games.product_sources import (
    PostgresCanonicalGamesStore,
    SOURCE_COLUMNS,
)
from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
from test_product_feedback_jobs import EncounterReceiver, FakeClock
from test_product_game_lapse_postgres import owned_postgres
from tempo_legacy_database.adapters.games.product_feedback import SCHEMA_SQL
from psycopg import sql
from test_product_source_identity import inputs


@pytest.fixture
def postgres_workflow(owned_postgres):
    """Isolate this source contract inside the labelled, disposable owner fixture."""
    schema = "source_identity_" + uuid.uuid4().hex
    options = owned_postgres.connection_options
    with psycopg.connect(**options, autocommit=True) as database:
        database.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        database.execute(SCHEMA_SQL)

    @contextmanager
    def connections():
        with psycopg.connect(
            **options, autocommit=True, options=f"-c search_path={schema}"
        ) as database:
            yield database

    def count(table):
        with connections() as database:
            return database.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]

    try:
        yield SimpleNamespace(connections=connections, count=count)
    finally:
        with psycopg.connect(**options, autocommit=True) as database:
            database.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_canonical_publication_rollback_and_retained_replay_share_one_frozen_source(
    postgres_workflow,
):
    with postgres_workflow.connections() as database:
        migration = (
            Path(__file__).resolve().parents[2]
            / "database/tempo_legacy_database/ddl/migrations/001_initial.sql"
        )
        statement = next(
            line
            for line in migration.read_text().splitlines()
            if line.startswith("CREATE TABLE imported_games (")
        )
        database.execute(statement)
        database.execute(
            "CREATE FUNCTION reject_delivery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'identity_fault_before_delivery'; END $$"
        )
        database.execute(
            "CREATE TRIGGER identity_fault BEFORE INSERT ON product_feedback_deliveries FOR EACH ROW EXECUTE FUNCTION reject_delivery()"
        )

    @contextmanager
    def connections():
        with postgres_workflow.connections() as database:
            database.row_factory = tempo_row_factory
            yield database

    source, request, snapshot = inputs()
    store, clock, receiver = (
        PostgresCanonicalGamesStore(connections),
        FakeClock(),
        EncounterReceiver(),
    )
    page = LichessGamePage(
        provider="lichess",
        account=request.account,
        window=request.window,
        records=(source,),
        rejections=(),
        continuation=None,
        complete=True,
        saturated=False,
    )
    coordinator = Coordinator(
        providers={"lichess": SimpleNamespace(fetch_page=lambda **kwargs: page)},
        normalizer=WorkflowNormalizer(),
        source_identity=store,
        comparer=WorkflowComparer(),
        snapshots=SimpleNamespace(snapshot=lambda repertoire: snapshot),
        store=store,
        learning=receiver,
        clock=clock,
        encounter_feedback=True,
    )
    store.enqueue(request)
    claimed = store.claim_job(clock.now(), clock.now() + 120)
    with pytest.raises(
        psycopg.errors.RaiseException, match="identity_fault_before_delivery"
    ):
        coordinator.capture_page(claimed)
    assert store.load_job(request.job_id).pages_committed == 0
    for table in (
        "imported_games",
        "product_feedback_sources",
        "product_feedback_captures",
        "product_feedback_deliveries",
    ):
        assert postgres_workflow.count(table) == 0
    with connections() as database:
        database.execute("DROP TRIGGER identity_fault ON product_feedback_deliveries")
    coordinator.capture_page(claimed)
    original = store.pending_delivery(clock.now())
    with connections() as database:
        raw = dict(
            database.execute(
                "SELECT " + SOURCE_COLUMNS + " FROM imported_games"
            ).fetchone()
        )
    assert (
        original.encounter.source.source_digest
        == fingerprint(raw)
        != source.source_digest
    )
    retained = retained_canonical_game(
        {"game": raw, "authority": {"workspace_id": "workspace"}}
    )
    findings = WorkflowComparer().compare(
        retained, snapshot, record=source, spec=request
    )
    assert findings == store.source(original.source_key).findings
    replay = original.encounter
    assert (
        replay.source.provider,
        replay.source.game_id,
        replay.source.source_digest,
    ) == ("lichess", retained.game_id, fingerprint(raw))
    # Fresh store/connection after commit must retain the exact original command.
    restored = PostgresCanonicalGamesStore(connections)
    assert restored.pending_delivery(clock.now()) == original
    coordinator.deliver(original)
    assert (
        receiver.accept_encounter(replay).outcome == "duplicate"
        and receiver.first_introductions == 1
    )
    newer = source.model_copy(update={"source_revision": 8})
    page = page.model_copy(update={"records": (newer,)})
    store.enqueue(request.model_copy(update={"job_id": "overlap"}))
    FeedbackRunner(coordinator).run_once()
    assert (
        postgres_workflow.count("imported_games")
        == postgres_workflow.count("product_feedback_deliveries")
        == 1
    )
    assert postgres_workflow.count("product_feedback_captures") == 2
