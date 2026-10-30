"""One native diagnosis of bounded/native queue-plan guard parity.

Uses the existing disposable source/publication/mode fixture. This is source
SQL evidence, not a packaged-service or real-browser qualification.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

import psycopg
from psycopg.conninfo import make_conninfo

from test_product_delivery_qualification import native_handoff, owned_postgres
from test_product_recombination_postgres import feedback_lineage_owner


PRESENTATION_FIELDS = (
    "id", "card_id", "position", "content_type", "admission_kind",
    "gameplay_priority_reason", "cycle", "attempt_state", "attempt_failed",
    "review_completed", "revision", "issued", "presented", "taught", "eligible",
    "urgent_determined_at", "urgent_stream", "urgent_effect_id",
)


def _value_fact(value):
    return {"value": value[:200] if isinstance(value, str) else value, "type": type(value).__name__}


def _field_facts(rows):
    return [{field: _value_fact(row.get(field))
             for field in PRESENTATION_FIELDS} for row in rows[:2]]


def _first_difference(prepared, current):
    left = {row["id"]: row for row in prepared}
    right = {row["id"]: row for row in current}
    for entry_id in sorted(left.keys() | right.keys()):
        for field in PRESENTATION_FIELDS:
            before = left.get(entry_id, {}).get(field)
            after = right.get(entry_id, {}).get(field)
            if entry_id not in left or entry_id not in right or type(before) is not type(after) or before != after:
                return {"queue_entry_id": entry_id, "field": field,
                        "prepared": _value_fact(before), "writer": _value_fact(after)}
    return None


def test_native_qualified_unseen_queue_randomization_bounded_and_writer_facts_match(native_handoff, monkeypatch):
    from app import queue_commands
    from app.product_composition import feedback_release
    from app.product_composition.game_review import SystemClock
    from app.services import frozen_opening_order, postgres_queue_refresh, queue_application, queue_next_card, queue_ordering
    from tempo_legacy_database.adapters.queue_refresh import presentation
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from tempo_legacy_database.adapters.study.issuance import attempt_issuance

    case, clock = native_handoff, SystemClock()
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    monkeypatch.setattr(queue_next_card, "_prepare", None)
    monkeypatch.setattr(queue_next_card, "_source_pending", None)
    with case.case.transaction() as database:
        owned_read_dsn = make_conninfo(database.raw.info.dsn, password=database.raw.info.password)

    @contextmanager
    def bounded_reader():
        # The existing fixture transaction already executes an ownership SELECT.
        # A fresh connection to that same owned DB can set access mode first.
        with psycopg.connect(owned_read_dsn, row_factory=tempo_row_factory) as raw:
            raw.execute("SET TRANSACTION READ ONLY")
            database = PostgresConnection(raw, read_only=True)
            database.execute_native("""SELECT set_config('transaction_timeout','250ms',true),
                set_config('lock_timeout','100ms',true),set_config('statement_timeout','250ms',true)""")
            yield database

    # Only authored fixture policy changes here; every effect, introduction and
    # attempt origin below is written by its actual semantic owner.
    with case.connections.writer() as database:
        database.execute_native("UPDATE repertoires SET new_cards_per_day=NULL WHERE id=%s", (case.case.repertoire_id,))
        assert tuple(database.execute_native("""SELECT COALESCE(repertoire.new_cards_per_day,settings.new_cards_per_day),
            settings.study_only_mode FROM repertoires repertoire CROSS JOIN settings
            WHERE repertoire.id=%s AND settings.id=1""", (case.case.repertoire_id,)).fetchone()) == (0, 1)
    feedback_release.register_feedback_next_card_priority(workspace=case.workspace, read=bounded_reader, clock=clock)
    for _ in range(2):  # The same bounded source handoff -> accepted Study work seam.
        prepared = queue_next_card.prepare_next_card_command({})
        with case.connections.writer() as database:
            result = queue_next_card.prepared_next_card_result(database, prepared)
            assert result["progressed"], result
            effect = PostgresGameFeedbackStore(database).get(case.original.command.key)
            if effect is not None and effect.progress is not None and effect.progress.introduction_applied_at:
                break
    with case.connections.writer() as database:
        store = PostgresGameFeedbackStore(database)
        effect = store.get(case.original.command.key)
        assert effect.status == "applied" and effect.progress.introduction_applied_at
        target = effect.progress.target
        day = queue_commands.current_study_day(database)
        entry = database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND queue_date=%s AND status='queued'",
            (target.card_id, day)).fetchone()[0]
        assert queue_commands.issue_queue_head(database, dict(queue_date=day, entry_id=entry,
            card_id=target.card_id, expected_revision=target.card_revision)) == {"issued": True}
        issued = attempt_issuance(database, entry, target.card_id, target.card_revision)
        assert issued is not None
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (target.card_id,)).fetchone()[0] == 0
        database_name = database.raw.info.dbname
    assert database_name.startswith("tempo_game_feedback_")
    owner = database_name.replace("_", "-")
    receipt_path = root / "test-results/collection-release" / ("randomization-parity-" + owner + ".json")
    report = dict(candidate=revision, fixture_owner=owner, database=database_name,
        fixture_receipt=str(root / "test-results/game-feedback-native" / owner / "fixture.json"),
        operation_id=case.original.operation_id, effect_key=case.original.command.key.model_dump(mode="json"),
        target=target.model_dump(mode="json"), issuance=issued.model_dump(mode="json"),
        transaction_timeout_ms=250, lock_timeout_ms=100, prepare_calls=0, publish_calls=0)

    # Observe the actual callback rows without replacing SQL, facts or guards.
    actual_rows = queue_application.presentation_rows
    callback_rows, publish_rows = [], []
    def observe_rows(database, *args, **kwargs):
        rows = actual_rows(database, *args, **kwargs)
        selected = [{field: row.get(field) for field in PRESENTATION_FIELDS} for row in rows]
        (callback_rows if database is None else publish_rows).extend(selected)
        return rows
    monkeypatch.setattr(queue_application, "presentation_rows", observe_rows)
    monkeypatch.setattr(postgres_queue_refresh, "background_read_connection", bounded_reader)
    monkeypatch.setattr(postgres_queue_refresh.read_storage, "background_read_connection", bounded_reader)

    try:
        report["prepare_calls"] += 1
        plan = postgres_queue_refresh._prepare_queue_randomization(day)
        with case.connections.writer() as database:
            frozen_token = list(frozen_opening_order.activation(database))
            native_rows = actual_rows(database, day)
            membership = presentation.current_membership(database, day)
            limits = dict(database.execute_native("""SELECT current_setting('transaction_timeout') AS transaction_timeout,
                current_setting('lock_timeout') AS lock_timeout,current_setting('statement_timeout') AS statement_timeout""").fetchone())
            report.update(prepared_token=plan["frozen_token"], writer_token=frozen_token,
                prepared_hash=plan["raw_membership_hash"], writer_hash=queue_ordering._queue_membership_hash(membership),
                prepared_presentation=plan["presentation_state"],
                writer_presentation=queue_ordering._queue_presentation_state(native_rows),
                prepared_pks=[row["id"] for row in callback_rows[:2]], native_pks=[row["id"] for row in native_rows[:2]],
                prepared_row_count=len(callback_rows), native_row_count=len(native_rows),
                prepared_fields=_field_facts(callback_rows), native_fields=_field_facts(native_rows),
                first_difference=_first_difference(callback_rows, native_rows),
                retry=bool(plan.get("retry")), entries_absent=plan["entries"] is None, writer_limits=limits)
            report["publish_calls"] += 1
            report["published"] = postgres_queue_refresh._publish_queue_randomization(database, day, plan)
        # Diagnostics are persisted only after the borrowed writer has exited.
        report["writer_closed"] = bool(database.raw.closed)
    except Exception as error:
        report["error"] = type(error).__name__
        raise
    finally:
        report["publish_fields"] = _field_facts(publish_rows)
        report["publish_row_count"] = len(publish_rows)
        report["publish_first_difference"] = _first_difference(callback_rows, publish_rows) if publish_rows else None
        guards = {"retry_clear": not report.get("retry", False),
            "frozen_token_equal": report.get("prepared_token") == report.get("writer_token"),
            "presentation_equal": report.get("prepared_presentation") == report.get("writer_presentation"),
            "raw_membership_equal": report.get("prepared_hash") == report.get("writer_hash")}
        report["guard_facts"] = guards
        report["first_failed_guard"] = next((name for name, passed in guards.items() if not passed), None)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps(dict(guard_receipt=str(receipt_path), published=report.get("published"),
            first_difference=report.get("first_difference"), error=report.get("error"))))
    assert report["writer_closed"]
    assert report["prepared_row_count"] <= 2 and report["native_row_count"] <= 2
    assert report["prepare_calls"] == report["publish_calls"] == 1
    assert report["prepared_token"] == report["writer_token"], report
    assert report["prepared_hash"] == report["writer_hash"], report
    assert report["prepared_presentation"] == report["writer_presentation"], report
    assert report["first_difference"] is None and report["publish_first_difference"] is None, report
    assert report["published"], report


def test_native_actual_join_target_keeps_bounded_and_native_owed_urgency(feedback_lineage_owner, monkeypatch):
    """An earned current target differs from the immutable original effect card."""
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    import chess
    from app.contracts.product_v02.learning import GameEncounter
    from app.games.feedback.coordinator import delivery_for
    from app.legacy_game_observation import capture_game
    from app.product_composition.feedback_maintenance import prepare_feedback_effect, apply_prepared_feedback_effect
    from app.product_composition.retained_game import retained_canonical_game
    from app.services import queue_application, queue_ordering
    from app.services.postgres_integrity import current_integrity_certificate
    from tempo_legacy_database.adapters.games.authority.headers import repertoire_metadata
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore, feedback_queue_priorities, owed_feedback_card_ids,
    )
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_intake
    from product_encounter_fixtures import qualified_feedback_source
    from test_product_recombination_postgres import feedback_lineage_fixture

    transaction = feedback_lineage_owner
    # The existing helper earns the join from real ordinary saves. It does not
    # seed a current map, alias or generated presentation; retain authored A.
    original, reader, _ = feedback_lineage_fixture(transaction, monkeypatch, legacy_superseded=False)
    observed, game_id = datetime.now(timezone.utc), 'lichess:priority-parity-'+uuid4().hex
    with transaction() as database:
        database.execute_native("""INSERT INTO imported_games(id,provider,provider_game_id,username,
            played_at,speed,rated,color,result,start_fen,moves_json,content_hash)
            VALUES(%s,'lichess',%s,'synthetic',%s,'rapid',0,'white','1/2-1/2',%s,'["d2d4"]',%s)""",
            (game_id, game_id.removeprefix('lichess:'), (observed-timedelta(days=1)).isoformat(), chess.STARTING_FEN, 'a'*64))
    def rows(statement, parameters=()):
        with reader() as database:
            return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]
    def metadata(repertoire_id):
        header = repertoire_metadata(rows, repertoire_id)[0]
        with reader() as database:
            certificate = current_integrity_certificate(database, repertoire_id, header['graph_generation'])
        return dict(header, integrity_current=certificate is not None, integrity_certificate=certificate)
    captured = capture_game(original.repertoire_id, game_id, (0,), reader=rows, metadata=metadata, now=observed)
    game = retained_canonical_game(captured)
    template = GameEncounter(key=dict(workspace_id=game.workspace_id,provider='lichess',game_id=game_id,
        decision_id=original.decision_id,occurrence_ply=1),source=dict(provider='lichess',game_id=game_id,
        source_revision=game.source_revision,source_digest=game.source_digest),decision=original,
        expected_uci='e2e4',played_uci='d2d4',observation='missed',detected_at=observed.isoformat(),
        occurred_at=game.played_at,payload_digest='e'*64)
    with qualified_feedback_source(transaction,capture=captured,command=template,clock=lambda:observed,
            capture_provider_source=True) as source:
        command, prepared = source.command, source.prepared
        target = prepared.qualification.selection.decision
        assert command.decision.presentation_id == original.presentation_id == 'A'
        assert target.presentation_id != original.presentation_id
        delivery = source.store.delivery(delivery_for(source.source.source_key, command).operation_id)
        original_bytes = delivery.command.model_dump_json()
        with transaction() as database:
            database.execute_native("SET LOCAL transaction_timeout='250ms'")
            database.execute_native("SET LOCAL lock_timeout='100ms'")
            accepted = current_owner_encounter_intake(database,retained_owner_capture=captured,
                snapshot=prepared.target_snapshot,clock=lambda:observed,
                prepared_decisions=prepared.prepared_decisions,qualification=prepared.qualification).accept_qualified(
                    command,qualification=prepared.qualification)
            effect = PostgresGameFeedbackStore(database).get(command.key)
        unit = prepare_feedback_effect(effect,read=transaction,capture=lambda *args:captured,
            clock=lambda:observed,qualification_prepare=lambda _:prepared)
        with transaction() as database:
            database.execute_native("SET LOCAL transaction_timeout='250ms'")
            database.execute_native("SET LOCAL lock_timeout='100ms'")
            available = apply_prepared_feedback_effect(database,unit,clock=lambda:observed,operation_admitted=True)
            effect = PostgresGameFeedbackStore(database).get(command.key)
            assert available.admission_status == 'available'
            assert effect.card_id == 'A' and effect.progress.target.card_id == target.presentation_id
        def bounded(statement, parameters=()):
            with reader() as database:
                return database.execute(statement, parameters).fetchall()
        report = dict(effect_card_id=effect.card_id,target_card_id=target.presentation_id,
            effect_key=command.key.model_dump(mode='json'),owner_receipt_id=accepted.owner_receipt_id)
        root = Path(__file__).resolve().parents[2]
        receipt_path = root/'test-results/collection-release'/('lineage-priority-parity-'+game_id.split(':')[-1]+'.json')
        try:
            with reader() as database:
                native_owed = owed_feedback_card_ids(database,(target.presentation_id,),include_regrouped=True)
                native_priority = feedback_queue_priorities(database,(target.presentation_id,))
                native_rows = [row for row in queue_application.presentation_rows(database,observed.date().isoformat())
                    if row['card_id']==target.presentation_id]
            bounded_owed = owed_feedback_card_ids(None,(target.presentation_id,),read=bounded,include_regrouped=True)
            bounded_priority = feedback_queue_priorities(None,(target.presentation_id,),read=bounded)
            bounded_rows = [row for row in queue_application.presentation_rows(None,observed.date().isoformat(),read=bounded)
                if row['card_id']==target.presentation_id]
            report.update(native_owed=sorted(native_owed),bounded_owed=sorted(bounded_owed),
                native_priority=native_priority,bounded_priority=bounded_priority,
                native_fields=_field_facts(native_rows),bounded_fields=_field_facts(bounded_rows),
                first_difference=_first_difference(bounded_rows,native_rows))
        finally:
            receipt_path.parent.mkdir(parents=True,exist_ok=True)
            receipt_path.write_text(json.dumps(report,indent=2,default=str))
            print(json.dumps(dict(lineage_guard_receipt=str(receipt_path),first_difference=report.get('first_difference'))))
        assert native_owed == bounded_owed == {target.presentation_id}
        assert native_priority == bounded_priority and target.presentation_id in native_priority
        assert len(native_rows)==len(bounded_rows)==1
        assert queue_ordering._queue_presentation_state(native_rows)==queue_ordering._queue_presentation_state(bounded_rows)
        assert report['first_difference'] is None, report
        assert source.store.delivery(delivery.operation_id).command.model_dump_json() == original_bytes
