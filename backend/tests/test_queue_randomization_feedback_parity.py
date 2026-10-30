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
