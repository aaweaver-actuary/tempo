"""Disposable full-schema shared-position Games carrier probe.

Run only in the coordinator's exclusive native window with an empty task-owned
TEMPO_POSITION_N2_DSN and N2's shared fixture installed. Games qualification is
provided by the pure policy fixture; this proves Study transactions, not Games
producer source qualification. No review, grade, effect, or completion is forged.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest


def test_native_transposition_again_alias_issuance_and_settlement(monkeypatch):
    if not os.environ.get("TEMPO_POSITION_N2_DSN"):
        pytest.skip("requires explicit empty task-owned TEMPO_POSITION_N2_DSN")
    import chess
    import psycopg
    from test_product_game_lapse import fixture, qualification_for
    from app.contracts.product_v02.learning import GameEncounter
    from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
    from app.study.encounter_feedback import EncounterFeedback
    from app.services import (
        postgres_opening_evidence as evidence,
        learning_review_adapter,
    )
    from app.services.opening_decision_evidence import decision_manifest
    from app.services.queue_next_card import prepare_queue_head
    from app.queue_commands import issue_queue_head
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.opening_evidence import (
        storage as evidence_storage,
    )
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import (
        prepare_position_source,
    )
    from tempo_legacy_database.adapters.postgres_store import (
        PostgresConnection,
        tempo_row_factory,
    )
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )
    from tempo_legacy_database.adapters.study.game_feedback_authority import (
        CurrentOwnerDecisions,
    )
    from tempo_legacy_database.adapters.study.recombination import (
        prepare_recombination_review,
    )
    from tempo_evidence_math.position_learning import Hold

    fixture_dir = (
        Path(__file__).resolve().parents[2] / "database" / "tests" / "position_study"
    )
    monkeypatch.syspath_prepend(str(fixture_dir))
    from acceptance import _dsn
    from italian_acceptance import seed_disposable_source

    dsn = _dsn()
    now = datetime.now(timezone.utc)
    routes = [
        ("d2d4", "d7d5", "g1f3", "g8f6", "c1f4"),
        ("d2d4", "g8f6", "g1f3", "d7d5", "c1f4"),
    ]

    @contextmanager
    def reader(*args, **kwargs):
        with psycopg.connect(
            dsn, user="tempo_reader", row_factory=tempo_row_factory
        ) as conn:
            yield PostgresConnection(conn, read_only=True)

    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", dsn)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", dsn)
    monkeypatch.setattr(connections, "read_connection", reader)
    monkeypatch.setattr(connections, "background_read_connection", reader)
    monkeypatch.setattr(evidence_storage, "read_connection", reader)
    monkeypatch.setattr(learning_review_adapter, "read_connection", reader)
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        db, _, physical, day = seed_disposable_source(
            raw, dsn, routes, ("order-a", "order-b"), repertoire_id="j2-alias", now=now
        )
        raw.execute("UPDATE cards SET introduced_at=NULL,state='new' WHERE id='card-0'")
        raw.commit()
        source = prepare_position_source(db, "j2-alias")
        assert not isinstance(source, Hold), source
        board = chess.Board()
        for uci in routes[0][:4]:
            board.push_uci(uci)
        position = " ".join(board.fen(en_passant="legal").split()[:4])
        original = [
            item
            for item in source.snapshot.decision_presentations
            if item.presentation_id == "card-0" and item.position_key == position
        ]
        alias = [
            item
            for item in source.snapshot.decision_presentations
            if item.presentation_id == "card-1" and item.position_key == position
        ]
        assert len(original) == len(alias) == 1
        assert original[0].decision_id != alias[0].decision_id
        template = fixture("encounters/unintroduced-missed", GameEncounter)
        key = template.key.model_copy(
            update={"decision_id": original[0].decision_id, "occurrence_ply": 4}
        )
        command = template.model_copy(
            update={
                "key": key,
                "decision": original[0],
                "expected_uci": "c1f4",
                "played_uci": "c2c4",
                "detected_at": now.isoformat(),
                "occurred_at": (now - timedelta(days=1)).isoformat(),
            }
        )
        qualification = qualification_for(command)

        class PurePolicyOwner(CurrentOwnerDecisions):
            def current_qualified(self, command, qualification):
                assert qualification.matches(command)
                return (original[0],)

        decisions = object.__new__(PurePolicyOwner)
        decisions.database, decisions._position_source = db, source
        owner = EncounterFeedback(
            PostgresGameFeedbackStore(db),
            decisions,
            clock=lambda: now,
            introductions=None,
            introduction_policy=None,
            position_intake=decisions,
        )
        accepted = owner.accept_qualified(command, qualification=qualification)
        assert (
            accepted.disposition == "introduced_again"
            and accepted.progress.position_grade_effect_id
        )
        raw.commit()
        carrier = decisions.prepare_position_carrier(db, qualification)
        assert carrier and carrier[0].presentation_id == "card-1"
        promoted = owner.refresh_encounter(
            command.key, qualification=qualification, position_carrier=carrier
        )
        assert promoted.progress.target.card_id == "card-1"
        raw.commit()
        row = raw.execute(
            "SELECT id,admission_repertoire_id FROM daily_queue WHERE card_id='card-1' AND status='queued'"
        ).fetchone()
        assert row and row["admission_repertoire_id"] == "j2-alias"
        raw.commit()
        envelope = {
            "queue_date": day,
            "entry_id": row["id"],
            "card_id": "card-1",
            "expected_revision": 1,
        }
        head = prepare_queue_head(envelope)
        assert head.mode == "position", head
        result = issue_queue_head(db, head)
        assert result["issued"], result
        raw.commit()
        issued = prepare_queue_head(envelope).proposal
        scope = raw.execute(
            """SELECT s.*,q.effective_trained_color FROM opening_evidence_presentations s
            JOIN opening_evidence_queue_contexts q ON q.presentation_snapshot_id=s.id
            WHERE q.queue_entry_id=%s AND q.repertoire_id='j2-alias' """,
            (row["id"],),
        ).fetchone()
        manifest = evidence.issued_position_manifest(
            issued,
            decision_manifest(evidence._manifest_snapshot(dict(scope)), "j2-alias"),
        )
        stamp = datetime.now(timezone.utc).isoformat()
        events = [
            dict(
                sequence=i + 1,
                decision_index=i,
                decision_id=d["decision_id"],
                expected_uci=d["expected_uci"],
                kind="first_response",
                observed_at=stamp,
                response_uci=d["expected_uci"],
                disposition="expected",
            )
            for i, d in enumerate(manifest["decisions"])
        ]
        completion = dict(
            attempt_id=issued.attempt_id,
            manifest=manifest,
            origin_queue_entry_id=row["id"],
            queue_entry_id=row["id"],
            started_at=stamp,
            study_timezone="UTC",
            source="live",
            events=events,
            terminal={
                "state": "complete",
                "final_sequence": len(events),
                "ended_at": stamp,
            },
        )
        raw.commit()
        validated = OpeningEvidenceCheckpoint.model_validate(completion)
        prepared_manifest = evidence.prepare_checkpoint(validated)
        with reader() as read_db:
            checkpoint = evidence.prepare_position_checkpoint(read_db, validated)
        payload = {
            "card_id": "card-1",
            "review": {
                "outcome": "correct",
                "guided": False,
                "queue_entry_id": row["id"],
                "expected_revision": 1,
                "attempt_id": issued.attempt_id,
                "opening_evidence_completion": completion,
            },
        }
        payload = prepare_recombination_review(payload, read_connection=reader)
        payload.update(
            prepared_manifest=prepared_manifest, prepared_position_checkpoint=checkpoint
        )
        saved = learning_review_adapter.submit_legacy_review(db, payload)
        assert saved["persisted"]
        raw.commit()
        effect = PostgresGameFeedbackStore(db).get(command.key)
        assert effect.status == "settled" and effect.settlement_review_id is not None
        assert raw.execute(
            "SELECT 1 FROM review_attempt_receipts WHERE review_id=%s AND queue_entry_id=%s",
            (effect.settlement_review_id, row["id"]),
        ).fetchone()
        assert (
            raw.execute(
                "SELECT count(*) FROM study_position_grades WHERE game_effect_key=%s AND grade='again'",
                (command.key.logical_id(),),
            ).fetchone()[0]
            == 1
        )
        assert {
            row["id"]: row["fsrs_card_json"]
            for row in raw.execute("SELECT id,fsrs_card_json FROM cards").fetchall()
        } == physical


def test_native_unseen_position_uses_canonical_encounter_exception_once(monkeypatch):
    """Real seed holds precede canonical admission; policy qualification is synthetic."""
    if not os.environ.get("TEMPO_POSITION_N2_DSN"):
        pytest.skip("requires explicit empty task-owned TEMPO_POSITION_N2_DSN")
    import chess
    import psycopg
    from types import SimpleNamespace
    from test_product_game_lapse import fixture, qualification_for
    from app.contracts.product_v02.learning import GameEncounter
    from app.product_composition.feedback_maintenance import (
        prepare_feedback_effect,
        apply_prepared_feedback_effect,
    )
    from app.study.encounter_feedback import EncounterFeedback
    from app.services.queue_next_card import prepare_queue_head
    from app.queue_commands import issue_queue_head
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.postgres_store import (
        PostgresConnection,
        tempo_row_factory,
    )
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore,
    )
    from tempo_legacy_database.adapters.study.game_feedback_authority import (
        CurrentOwnerDecisions,
    )

    fixture_dir = (
        Path(__file__).resolve().parents[2] / "database" / "tests" / "position_study"
    )
    monkeypatch.syspath_prepend(str(fixture_dir))
    from acceptance import _dsn
    from italian_acceptance import seed_disposable_source

    dsn = _dsn()
    now = datetime.now(timezone.utc)
    routes = [
        ("d2d4", "d7d5", "g1f3", "g8f6", "c1f4"),
        ("d2d4", "g8f6", "g1f3", "d7d5", "c1f4"),
    ]

    @contextmanager
    def reader(*args, **kwargs):
        with psycopg.connect(
            dsn, user="tempo_reader", row_factory=tempo_row_factory
        ) as conn:
            yield PostgresConnection(conn, read_only=True)

    monkeypatch.setattr(connections, "read_connection", reader)
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        db, source, physical, day = seed_disposable_source(
            raw,
            dsn,
            routes,
            ("order-a", "order-b"),
            repertoire_id="j2-unseen",
            now=now,
            introduced=False,
        )
        raw.execute(
            "UPDATE settings SET study_only_mode=1,new_cards_per_day=0 WHERE id=1"
        )
        scope_revision = raw.execute(
            "SELECT scope_source_revision FROM repertoires WHERE id='j2-unseen'"
        ).fetchone()[0]
        assert (
            raw.execute(
                "SELECT count(*) FROM study_position_states WHERE repertoire_id='j2-unseen'"
            ).fetchone()[0]
            == 0
        )
        assert raw.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
        position = " ".join(chess.Board().fen(en_passant="legal").split()[:4])
        target = [
            item
            for item in source.snapshot.decision_presentations
            if item.presentation_id == "card-0" and item.position_key == position
        ]
        assert len(target) == 1 and target[0].introduction == "unintroduced"
        seed = raw.execute(
            """SELECT disposition,hold_reason FROM study_position_seed_outcomes
            WHERE repertoire_id='j2-unseen' AND variant='standard' AND position=%s""",
            (position,),
        ).fetchone()
        assert (seed["disposition"], seed["hold_reason"]) == (
            "hold",
            "no_valid_current_introduced_donor",
        )
        raw.commit()
        template = fixture("encounters/unintroduced-missed", GameEncounter)
        key = template.key.model_copy(
            update={"decision_id": target[0].decision_id, "occurrence_ply": 1}
        )
        command = template.model_copy(
            update={
                "key": key,
                "decision": target[0],
                "expected_uci": "d2d4",
                "played_uci": "e2e4",
                "detected_at": now.isoformat(),
                "occurred_at": (now - timedelta(days=1)).isoformat(),
            }
        )
        qualification = qualification_for(command)

        class PurePolicyOwner(CurrentOwnerDecisions):
            def current_qualified(self, command, qualification):
                assert qualification.matches(command)
                return (target[0],)

            def introduction_revision(self, repertoire_id):
                assert repertoire_id == "j2-unseen"
                return scope_revision + 1

        decisions = object.__new__(PurePolicyOwner)
        decisions.database, decisions._position_source, decisions._prepared_targets = (
            db,
            source,
            (),
        )
        owner = EncounterFeedback(
            PostgresGameFeedbackStore(db),
            decisions,
            clock=lambda: now,
            introductions=None,
            introduction_policy=None,
            position_intake=decisions,
        )
        accepted = owner.accept_qualified(command, qualification=qualification)
        assert (
            accepted.disposition == "first_introduction"
            and accepted.reason == "position_introduction_pending"
        )
        raw.commit()
        with reader() as read_db:
            observed = PostgresGameFeedbackStore(read_db).get(command.key)
        prepared_policy = SimpleNamespace(
            target_capture=None,
            target_snapshot=None,
            qualification=qualification,
            prepared_decisions=decisions,
        )
        prepared = prepare_feedback_effect(
            observed,
            read=reader,
            capture=None,
            clock=lambda: now,
            qualification_prepare=lambda command: prepared_policy,
        )
        assert prepared.reason is None and prepared.position_introduction is not None
        assert prepared.prepared_introduction.reason is None
        receipt = apply_prepared_feedback_effect(
            db, prepared, clock=lambda: now, operation_admitted=True
        )
        assert (
            receipt.admission_status == "available"
            and receipt.progress.introduction_applied_at is not None
        )
        assert (
            receipt.progress.position_grade_effect_id is None
            and receipt.progress.scheduling_review_id is None
        )
        raw.commit()
        state = raw.execute(
            "SELECT position,introduced_at FROM study_position_states WHERE repertoire_id='j2-unseen'"
        ).fetchall()
        assert (
            len(state) == 1
            and state[0]["position"] == position
            and state[0]["introduced_at"] is not None
        )
        queue = raw.execute(
            "SELECT id,card_id,admission_repertoire_id,gameplay_priority_reason FROM daily_queue WHERE queue_date=%s AND status='queued'",
            (day,),
        ).fetchall()
        assert (
            len(queue) == 1
            and queue[0]["card_id"] == "card-0"
            and queue[0]["admission_repertoire_id"] == "j2-unseen"
        )
        assert queue[0]["gameplay_priority_reason"] == "product_game_encounter"
        assert (
            raw.execute("SELECT count(*) FROM study_position_grades").fetchone()[0] == 0
        )
        assert raw.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
        assert tuple(
            raw.execute(
                "SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1"
            ).fetchone()
        ) == (1, 0)
        assert {
            row["id"]: row["fsrs_card_json"]
            for row in raw.execute("SELECT id,fsrs_card_json FROM cards").fetchall()
        } == physical
        raw.commit()
        envelope = {
            "queue_date": day,
            "entry_id": queue[0]["id"],
            "card_id": "card-0",
            "expected_revision": 1,
        }
        head = prepare_queue_head(envelope)
        assert head.mode == "position", head
        result = issue_queue_head(db, head)
        assert result["issued"], result
        raw.commit()
        replay_head = prepare_queue_head(envelope)
        assert replay_head.mode == "replay", replay_head
        plan = replay_head.proposal
        assert plan.to_wire() == result["position_presentation"]
        assert (
            plan.original_card_id == "card-0" and plan.queue_entry_id == queue[0]["id"]
        )
        assert (
            plan.target.key.repertoire_id == "j2-unseen"
            and plan.target.key.position == position
        )
        assert plan.target.expected_uci == "d2d4"
        assert plan.decision_slots[0].definition == plan.target
        assert any(w.definition == plan.target for w in plan.due_positions)
        for witness in plan.due_positions:
            saved = raw.execute(
                """SELECT due_at,introduced_at FROM study_position_states
                WHERE repertoire_id=%s AND variant=%s AND position=%s""",
                (
                    witness.definition.key.repertoire_id,
                    witness.definition.key.variant,
                    witness.definition.key.position,
                ),
            ).fetchone()
            assert saved is not None
            assert witness.due == (
                saved["introduced_at"] is not None
                and saved["due_at"] <= witness.issued_at
            )
            if witness.definition != plan.target:
                assert not witness.due
        assert (
            raw.execute(
                "SELECT count(*) FROM study_position_states WHERE repertoire_id='j2-unseen'"
            ).fetchone()[0]
            == 1
        )
        assert (
            raw.execute("SELECT count(*) FROM study_position_grades").fetchone()[0] == 0
        )
        assert raw.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
        raw.commit()
        replay = owner.accept_qualified(command, qualification=qualification)
        assert (
            replay.outcome == "duplicate"
            and replay.progress.introduction_applied_at
            == receipt.progress.introduction_applied_at
        )
        raw.commit()
        with reader() as read_db:
            observed = PostgresGameFeedbackStore(read_db).get(command.key)
        prepared_again = prepare_feedback_effect(
            observed,
            read=reader,
            capture=None,
            clock=lambda: now,
            qualification_prepare=lambda command: prepared_policy,
        )
        assert prepared_again.reason is None
        replayed = apply_prepared_feedback_effect(
            db, prepared_again, clock=lambda: now, operation_admitted=True
        )
        assert (
            replayed.progress.introduction_applied_at
            == receipt.progress.introduction_applied_at
        )
        raw.commit()
        assert (
            raw.execute(
                "SELECT count(*) FROM study_position_states WHERE repertoire_id='j2-unseen'"
            ).fetchone()[0]
            == 1
        )
        assert (
            raw.execute(
                "SELECT count(*) FROM daily_queue WHERE queue_date=%s AND status='queued'",
                (day,),
            ).fetchone()[0]
            == 1
        )
        assert raw.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
