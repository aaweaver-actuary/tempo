"""Retained-history composition through real source, Study and receipt owners."""
import pytest
from datetime import date

from app.product_composition.retained_game import retained_canonical_game
from app.repertoire import project_capture
from product_encounter_fixtures import retained_history_case
from test_product_feedback_integration import _issue, _ordinary_review
from test_product_game_lapse_postgres import owned_postgres


@pytest.mark.parametrize('state,command_kind',[
    ('introduced','encounter'),('unintroduced','encounter'),('introduced','lapse'),
])
def test_qualified_global_publication_reaches_exact_study_consequence_and_real_settlement(
    owned_postgres,monkeypatch,state,command_kind,
):
    """One complete source/owner/queue proof, separate from actual provider HTTP."""
    from contextlib import contextmanager
    from datetime import timedelta
    import hashlib
    import chess
    import chess.pgn
    from app import queue_commands
    from app.contracts.product_v02.games import GameRecord, GameWindow
    from app.games.feedback.ports import SourceState
    from app.games.feedback.review import SyncConfiguration
    from app.product_composition.provider_priority import prepare_feedback_qualification
    from app.product_composition.feedback_maintenance import prepare_feedback_effect, apply_prepared_feedback_effect
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.study import issuance
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_intake
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store,'configured',lambda:True)
    # Parametrized cases share a workspace ledger; keep their fixed clocks in
    # actual causal order so an earlier fixture is not a future issuance.
    determined = NOW+timedelta(minutes=6 if command_kind=='lapse' else 3 if state=='unintroduced' else 0)
    timeline = [determined-timedelta(minutes=1)]
    actual_stamp = issuance.stamp_issued_head
    monkeypatch.setattr(issuance,'stamp_issued_head',
        lambda database,entry,card,revision:actual_stamp(database,entry,card,revision,now=timeline[0]))
    if hasattr(queue_commands,'current_study_day'):
        monkeypatch.setattr(queue_commands,'current_study_day',lambda *args,**kwargs:NOW.date().isoformat())
    with retained_history_case(owned_postgres,state=state,ordinary_limit=0,study_only=True) as case:
        @contextmanager
        def raw_reads():
            with case.transaction() as database:
                yield database.raw
        with case.transaction() as database:
            protected = _issue(database,case.protected_id)
        timeline[0] = determined
        captured = case.capture(case.game_ids[0]); original = case.command(captured)
        canonical = retained_canonical_game(captured)
        if command_kind == 'lapse':
            from app.games.feedback.comparison import compare_game
            from app.contracts.product_v02.learning import GameLapse
            target_snapshot = project_capture(captured,comparison_game=canonical)
            finding, = compare_game(canonical,provider='lichess',time_precision='second',snapshot=target_snapshot)
            assert finding.kind == 'confirmed_miss'
            original = GameLapse(key=original.key,finding=finding,detected_at=original.detected_at,
                occurred_at=original.occurred_at,payload_digest=original.payload_digest)
        raw = captured['game']
        pgn = chess.pgn.Game(); pgn.setup(chess.Board(canonical.starting_fen))
        pgn.headers.update(White='synthetic',Black='opponent',Result='*')
        pgn.add_variation(chess.Move.from_uci(original.played_uci if command_kind=='encounter'
                                             else original.finding.played_uci))
        text = str(pgn)
        record = GameRecord(source_game_id=raw['provider_game_id'],source_revision=1,
            source_digest=hashlib.sha256(text.encode()).hexdigest(),pgn=text,
            played_at=canonical.played_at,time_precision='second')
        source = SourceState(source_key=case.card_id+'-source',version=1,original=record,
            record_fingerprint=record.source_digest,normalized=canonical,findings=(),
            comparison_revision='original-synthetic-capture',effects=(original,),
            encounters=(original,) if command_kind=='encounter' else ())
        config = SyncConfiguration(workspace_id=canonical.workspace_id,provider='lichess',account='synthetic',
            window=GameWindow(start_at=(NOW-timedelta(days=2)).isoformat(),end_at=NOW.isoformat(),precision='second'),
            repertoire_ids=(case.repertoire_id,),speeds=('rapid',),rated=None,interval_seconds=None)
        PostgresReviewStore(raw_reads).configure(config,0)
        try:
            with case.transaction() as database:
                database.execute_native('INSERT INTO product_feedback_sources(source_key,version,payload) '
                    'VALUES(%s,1,%s::jsonb)',(source.source_key,source.model_dump_json()))
            capture = lambda repertoire,game,plies:case.capture(game)
            prepared = prepare_feedback_qualification(original,read=case.transaction,capture=capture,
                clock=lambda:determined,determination=determined.isoformat(),stream='forward')
            assert prepared.qualification.selection.ply == 1
            assert prepared.qualification.selection.decision.presentation_id == case.card_id
            assert prepared.qualification.selection.selected_scope[0].snapshot_id.startswith('publication:')
            assert prepared.target_snapshot.membership.snapshot_id.startswith('product:')
            # Every executable/chess proof has finished. The writer may only
            # recheck prepared source/version/target facts for either old kind.
            def writer_replay_forbidden(*args,**kwargs):
                pytest.fail('qualified writer replayed the target executable')
            prepared.prepared_decisions._validate_executable = writer_replay_forbidden
            with case.transaction() as database:
                owner = current_owner_encounter_intake(database,retained_owner_capture=prepared.target_capture,
                    snapshot=prepared.target_snapshot,clock=lambda:determined,qualification=prepared.qualification,
                    prepared_decisions=prepared.prepared_decisions)
                accepted = owner.accept_qualified(original,qualification=prepared.qualification)
                observed = PostgresGameFeedbackStore(database).get(original.key)
                assert accepted.admission_status == 'pending'
            unit = prepare_feedback_effect(observed,read=case.transaction,capture=capture,clock=lambda:determined,
                qualification_prepare=lambda command:prepared)
            with case.transaction() as database:
                database.execute_native("SET LOCAL transaction_timeout='250ms'")
                applied = apply_prepared_feedback_effect(database,unit,clock=lambda:determined,operation_admitted=True)
                assert applied.admission_status == 'available'
                assert applied.disposition == ('introduced_again' if state=='introduced' else 'first_introduction')
                assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                    (case.card_id,)).fetchone()[0] == int(state=='introduced')
                assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0]
            with case.transaction() as database:
                _ordinary_review(database,case.protected_id,protected,'qualified-protected:'+case.card_id)
            timeline[0] = determined+timedelta(minutes=1)
            with case.transaction() as database:
                target = _issue(database,case.card_id)
                first = _ordinary_review(database,case.card_id,target,'qualified-target:'+case.card_id)
                replay = _ordinary_review(database,case.card_id,target,'qualified-target:'+case.card_id)
                assert replay['review_id'] == first['review_id']
            with case.transaction() as database:
                effect = PostgresGameFeedbackStore(database).get(original.key)
                assert effect.status == 'settled' and effect.settlement_review_id == first['review_id']
                assert (effect.encounter or effect.lapse) == original and effect.progress.first_issued_ordinal == 1
                assert effect.progress.settlement.attempt.queue_entry_id == target
                assert database.execute_native('SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s',
                    (case.card_id,)).fetchone()[0] == 1
        finally:
            with case.transaction() as database:
                database.execute_native('DELETE FROM product_feedback_sources WHERE source_key=%s',(source.source_key,))
                database.execute_native('DELETE FROM product_feedback_configuration WHERE workspace_id=%s AND provider=%s',
                    (config.workspace_id,config.provider))


def test_native_already_issued_target_saves_before_reset_and_only_fresh_generation_settles(
    owned_postgres,monkeypatch,
):
    """Actual issuance/review/store race; complete source fences are proved above."""
    from datetime import timedelta
    from fsrs import Card
    from app import queue_commands
    from app.services.queue_application import seed_queue
    from app.study.encounter_feedback import EncounterFeedback
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.study import issuance
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from tempo_legacy_database.adapters.http_composition.practice import review_count
    from tempo_legacy_database.adapters.foreground_controls.prefix_splits import latest_failed_study_review_id
    from test_product_game_lapse import NOW,qualification_for
    monkeypatch.setattr(postgres_store,'configured',lambda:True)
    monkeypatch.setattr(queue_commands,'current_study_day',lambda *args,**kwargs:NOW.date().isoformat())
    timeline = [NOW-timedelta(minutes=3)]
    stamp = issuance.stamp_issued_head
    monkeypatch.setattr(issuance,'stamp_issued_head',
        lambda database,entry,card,revision:stamp(database,entry,card,revision,now=timeline[0]))
    with retained_history_case(owned_postgres,state='introduced') as case:
        with case.transaction() as database:
            # Seed an ordinary due target through the canonical queue owner.
            row = database.execute_native('SELECT fsrs_card_json FROM cards WHERE id=%s',(case.card_id,)).fetchone()
            due = Card.from_json(row[0]); due.due = NOW
            database.execute_native('UPDATE cards SET due_date=%s,fsrs_card_json=%s WHERE id=%s',
                (NOW.date().isoformat(),due.to_json(),case.card_id))
            protected = _issue(database,case.protected_id)
            _ordinary_review(database,case.protected_id,protected,'pre-race-protected:'+case.card_id,
                             observed_at=NOW-timedelta(minutes=2))
            seed_queue(database,NOW.date().isoformat())
        timeline[0] = NOW-timedelta(minutes=1)
        with case.transaction() as database:
            active = _issue(database,case.card_id)
            origin = issuance.attempt_issuance(database,active,case.card_id,1)
            assert origin is not None
        command = case.command(case.capture(case.game_ids[0]))
        witness = qualification_for(command)
        class Current:
            def current_qualified(self, original, qualification):
                assert qualification.matches(original)
                return (qualification.selection.decision,)
        def owner(database,now):
            return EncounterFeedback(PostgresGameFeedbackStore(database),Current(),clock=lambda:now,
                introductions=None,introduction_policy=None)
        with case.transaction() as database:
            accepted = owner(database,NOW).accept_qualified(command,qualification=witness)
            pending = owner(database,NOW).refresh_encounter(command.key)
            assert accepted.disposition == 'introduced_again' and pending.reason == 'active_attempt'
            assert pending.progress.scheduling_review_id is None
        finished_at = NOW+timedelta(minutes=1)
        with case.transaction() as database:
            original_result = _ordinary_review(database,case.card_id,active,'active-target:'+case.card_id,
                                                observed_at=finished_at)
            pending = PostgresGameFeedbackStore(database).get(command.key)
            assert pending.status == 'pending' and pending.settlement_review_id is None
            assert pending.review_id is None
            human_before = review_count(database,NOW.date().isoformat())
        reset_at = finished_at+timedelta(minutes=1)
        with case.transaction() as database:
            reset = owner(database,reset_at).refresh_encounter(command.key)
            assert reset.progress.scheduling_review_id is not None
            assert reset.progress.remediation_fence.after_issuance_sequence >= origin.issuance_sequence
            assert review_count(database,NOW.date().isoformat()) == human_before
            assert latest_failed_study_review_id(database,case.card_id) == reset.progress.scheduling_review_id
            schedule = tuple(database.execute_native('SELECT due_date,interval_days,fsrs_card_json '
                'FROM cards WHERE id=%s',(case.card_id,)).fetchone())
        with case.transaction() as database:
            replay = _ordinary_review(database,case.card_id,active,'active-target:'+case.card_id,
                                       observed_at=finished_at)
            assert replay['review_id'] == original_result['review_id']
            assert tuple(database.execute_native('SELECT due_date,interval_days,fsrs_card_json '
                'FROM cards WHERE id=%s',(case.card_id,)).fetchone()) == schedule
            assert PostgresGameFeedbackStore(database).get(command.key).status == 'applied'
        timeline[0] = reset_at+timedelta(minutes=1)
        with case.transaction() as database:
            fresh = _issue(database,case.card_id)
            assert fresh != active
            actual = issuance.attempt_issuance(database,fresh,case.card_id,1)
            assert actual.issuance_sequence > reset.progress.remediation_fence.after_issuance_sequence
            remedied = _ordinary_review(database,case.card_id,fresh,'fresh-target:'+case.card_id,
                                        observed_at=timeline[0]+timedelta(minutes=1))
        with case.transaction() as database:
            effect = PostgresGameFeedbackStore(database).get(command.key)
            assert effect.status == 'settled' and effect.settlement_review_id == remedied['review_id']
            assert effect.progress.settlement.attempt.queue_entry_id == fresh
            assert effect.progress.first_issued_ordinal == 1
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                (case.card_id,)).fetchone()[0] == 1
            assert database.execute_native('SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s',
                (case.card_id,)).fetchone()[0] == 2


def _study_refresh(case, capture, snapshot, command):
    """Separate Study transaction after committed intake; never called by intake."""
    from app.services.queue_priority_promotion import prepare_priority_promotion
    from app.services.queue_admission_adapter import current_owner_encounter_introductions
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_feedback
    from test_product_game_lapse import NOW
    prepared = prepare_priority_promotion(NOW.date().isoformat(), command.decision.presentation_id,
        read=case.transaction)
    with case.transaction() as database:
        introductions = current_owner_encounter_introductions(database,
            allowance="encounter_exception", paused=False)
        owner = current_owner_encounter_feedback(database, retained_owner_capture=capture,
            snapshot=snapshot, clock=lambda: NOW, introductions=introductions,
            introduction_policy=EncounterIntroductionPolicy("encounter_exception"),
            prepared_priority=prepared)
        return owner.refresh_encounter(command.key)


@pytest.fixture(autouse=True)
def normal_study_clock(monkeypatch):
    from app import queue_commands
    from app.services import learning_review_adapter
    from test_product_game_lapse import NOW
    class StudyDate(date):
        @classmethod
        def today(cls):
            return NOW.date()
    monkeypatch.setattr(queue_commands, "date", StudyDate)
    monkeypatch.setattr(learning_review_adapter, "date", StudyDate)


@pytest.mark.parametrize("state", ["unintroduced", "introduced", "later_learning"])
@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_retained_history_fixture_has_exact_current_mapping_and_protected_attempt(owned_postgres, monkeypatch, state, played):
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state=state, played=played) as case:
        with case.transaction() as database:
            _issue(database, case.protected_id)
        before = case.readback()
        captured = case.capture(case.game_ids[0])
        snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
        mapping, = snapshot.decision_presentations
        from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
        command = case.command(captured)
        with case.transaction() as database:
            current, = CurrentOwnerDecisions(database, retained_owner_capture=captured,
                snapshot=snapshot).current_encounter(command)
        assert mapping.presentation_id == case.card_id and mapping.available
        assert mapping.selected_response_uci == "e2e4"
        assert current.introduction == ("unintroduced" if state == "unintroduced" else "introduced")
        assert mapping.introduced_before_game is (state == "introduced")
        from app.product_composition.encounters import importance_from_encounters
        importance, = importance_from_encounters(workspace_id=command.key.workspace_id, account="synthetic",
            current_approved_decisions=(current,), encounters=(command, command))
        assert importance.facts.personal_encounters == 1
        assert len(importance.differing_response_sources) == (0 if played == "e2e4" else 1)
        assert len(importance.source_encounters) == 1
        assert captured["game"]["id"] == case.game_ids[0]
        assert captured["game"]["moves_json"] == '["' + played + '"]'
        assert case.readback() == before
        assert len(before["origins"]) == 1 and before["queue"][0][1] == case.protected_id
        assert sum(row[1] == case.card_id for row in before["reviews"]) == (0 if state == "unintroduced" else 1)


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_unintroduced_encounter_intake_commits_pending_without_queue_capability(
    owned_postgres, monkeypatch, played,
):
    from app.product_composition.encounters import receive_captured_encounter
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="unintroduced", played=played) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        before = case.readback()
        with case.transaction() as database:
            first = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
        with case.transaction() as database:
            duplicate = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
        assert first.observation_recorded and first.disposition == "first_introduction"
        assert first.admission_status == "pending" and first.available_by is None
        assert first.reason == "study_priority_pending"
        assert duplicate.owner_receipt_id == first.owner_receipt_id and duplicate.detected_at == first.detected_at
        assert case.readback() == before


def test_historical_priority_reaches_ordinary_study_without_rewinding_later_learning(owned_postgres, monkeypatch):
    from app.product_composition.encounters import receive_captured_encounter
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="later_learning") as case:
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
        before = case.readback(); capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            receipt = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
        assert receipt.disposition == "review_priority" and receipt.admission_status == "pending"
        assert receipt.available_by is None and case.readback() == before
        available = _study_refresh(case, capture, snapshot, command)
        assert available.admission_status == "available" and available.owner_receipt_id == receipt.owner_receipt_id
        after = case.readback()
        assert after["cards"] == before["cards"] and after["reviews"] == before["reviews"]
        assert tuple(row for row in after["origins"] if row[1] == case.protected_id) == before["origins"]
        assert len(tuple(row for row in after["origins"] if row[1] == case.card_id)) == 1
        with case.transaction() as database:
            _ordinary_review(database, case.protected_id, protected, "protected:"+case.card_id)
            target = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, target, "priority:"+case.card_id)["persisted"]
        with case.transaction() as database:
            replay = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
            assert replay.status == "settled" and replay.admission_status == "settled"
            assert replay.owner_receipt_id == receipt.owner_receipt_id and replay.detected_at == receipt.detected_at
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                (case.card_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_retained_day_only_history_reaches_the_published_workflow_fact_boundary(owned_postgres, played):
    from app.games.feedback.comparison import compare_game
    from app.repertoire import build_index
    with retained_history_case(owned_postgres, state="later_learning", played=played) as case:
        capture = case.capture(case.game_ids[0]); game = retained_canonical_game(capture)
        snapshot = project_capture(capture, comparison_game=game)
        assert snapshot.decision_presentations[0].introduced_before_game is False
        findings = compare_game(game, provider="lichess", time_precision="second", snapshot=snapshot,
            reentry_lookup=build_index(snapshot))
        # #489's existing consumer admits factual commands from these covered
        # non-lapse findings. Date-only chronology must not erase old relevance.
        assert len(findings) == 1 and findings[0].kind == "known_unintroduced"
        assert not findings[0].lapse_eligible


def test_ordinary_admission_winning_capture_race_is_not_a_new_lapse(owned_postgres, monkeypatch):
    from app.product_composition.encounters import receive_captured_encounter
    from app.services.queue_application import seed_queue
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    with retained_history_case(owned_postgres, state="unintroduced") as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            _issue(database, case.protected_id)
            seed_queue(database, NOW.date().isoformat())
            introduced = database.execute_native("SELECT introduced_at FROM cards WHERE id=%s",
                (case.card_id,)).fetchone()[0]
            assert introduced is not None
        before = case.readback()
        with case.transaction() as database:
            first = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
            duplicate = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
            assert first.disposition == "review_priority" and duplicate.owner_receipt_id == first.owner_receipt_id
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        after = case.readback()
        assert after["cards"] == before["cards"] and after["reviews"] == before["reviews"]


@pytest.mark.parametrize("played", ["e2e4", "d2d4"])
def test_targeted_zero_allowance_study_only_encounter_reaches_first_real_study_once(
    owned_postgres, monkeypatch, played,
):
    from threading import Event
    from app.product_composition.encounters import receive_admitted_encounter
    from app.services.queue_application import seed_queue
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    stopped = Event()
    with retained_history_case(owned_postgres, state="unintroduced", played=played,
            ordinary_limit=0, study_only=True) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
        before = case.readback()
        def receive(database):
            return receive_admitted_encounter(database, capture=capture, snapshot=snapshot,
                command=command, operation_stopped=stopped.is_set,
                clock=lambda: NOW)
        with case.transaction() as database:
            first = receive(database)
            assert first.disposition == "first_introduction" and first.admission_status == "pending"
            assert first.available_by is None
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
            assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone()) == (0, 1)
            assert database.execute_native("SELECT introduced_at FROM cards WHERE id=%s", (case.card_id,)).fetchone()[0] is None
        assert case.readback() == before
        available = _study_refresh(case, capture, snapshot, command)
        assert available.admission_status == "available" and available.owner_receipt_id == first.owner_receipt_id
        with case.transaction() as database:
            assert database.execute_native("SELECT introduced_at FROM cards WHERE id=%s", (case.card_id,)).fetchone()[0] == NOW.date().isoformat()
            # The ordinary seed/refresh must keep the targeted introduction and
            # protect the already presented unrelated head under saved controls.
            seed_queue(database, NOW.date().isoformat())
        introduced = case.readback()
        assert introduced["reviews"] == before["reviews"]
        assert tuple(row for row in introduced["origins"] if row[1] == case.protected_id) == before["origins"]
        with case.transaction() as database:
            assert receive(database).owner_receipt_id == first.owner_receipt_id
            _ordinary_review(database, case.protected_id, protected, "protected-first:"+case.card_id)
            target = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, target, "first-real:"+case.card_id)["persisted"]
        learned = case.readback()
        with case.transaction() as database:
            replay = receive(database)
            assert replay.owner_receipt_id == first.owner_receipt_id and replay.admission_status == "settled"
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 0
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1
        assert case.readback() == learned


def test_stopped_operation_defers_before_first_introduction_and_resumes_once(owned_postgres, monkeypatch):
    from threading import Event
    from app.product_composition.encounters import receive_admitted_encounter
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from tempo_legacy_database.adapters import postgres_store
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    stopped = Event(); stopped.set()
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0, study_only=True) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        before = case.readback()
        def receive(database):
            return receive_admitted_encounter(database, capture=capture, snapshot=snapshot,
                command=command, operation_stopped=stopped.is_set,
                clock=lambda: NOW)
        with pytest.raises(BackgroundAdmissionDeferred, match="operation is stopped"):
            with case.transaction() as database:
                receive(database)
        assert case.readback() == before
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        stopped.clear()
        with case.transaction() as database:
            first = receive(database)
            assert first.admission_status == "pending" and first.available_by is None
            assert receive(database).owner_receipt_id == first.owner_receipt_id
            assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        available = _study_refresh(case, capture, snapshot, command)
        assert available.admission_status == "available" and available.owner_receipt_id == first.owner_receipt_id
        with case.transaction() as database:
            assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1


@pytest.mark.parametrize("study_only", [False, True])
def test_pending_encounter_resumes_on_existing_durable_pause_and_task_fence_without_games(
    owned_postgres, monkeypatch, study_only,
):
    import builtins
    import json
    from datetime import timedelta
    from app.product_composition.encounters import receive_captured_encounter
    from app.product_composition.feedback_maintenance import KIND, execute_feedback_sweep
    from app.services.background_activity import set_control_in_transaction
    from app.services.defensive_analysis import DEFENSIVE_TASK_KINDS
    from app.services import durable_tasks
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.durable_tasks import storage
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(durable_tasks, "_now", lambda: NOW)
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0, study_only=study_only) as case:
        capture = case.capture(case.game_ids[0]); command = case.command(capture)
        snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
        with case.transaction() as database:
            pending = receive_captured_encounter(database, capture=capture, snapshot=snapshot, command=command,
                clock=lambda: NOW)
            assert pending.admission_status == "pending"
            task = durable_tasks.enqueue_task_in_transaction(database, KIND, case.repertoire_id, {"after": ""})
            unrelated_kind = "unrelated_optional_encounter_fixture"
            durable_tasks.enqueue_task_in_transaction(database, unrelated_kind, case.repertoire_id, {})
            _, unrelated_candidate, unrelated_parameters = storage.prepare_task_claim(
                unrelated_kind, None, NOW.isoformat(), DEFENSIVE_TASK_KINDS)
            assert (unrelated_candidate(database, [NOW.isoformat(), *unrelated_parameters]) is None) is study_only
            assert set_control_in_transaction(database, "durable", task["id"], "pause")
            _, read_candidate, parameters = storage.prepare_task_claim(KIND, None, NOW.isoformat(), DEFENSIVE_TASK_KINDS)
            assert read_candidate(database, [NOW.isoformat(), *parameters]) is None
        before = case.readback()
        with case.transaction() as database:
            assert set_control_in_transaction(database, "durable", task["id"], "resume")
            row = read_candidate(database, [NOW.isoformat(), *parameters])
            assert row["id"] == task["id"]
            storage.claim_lease(database, ("encounter-maintenance-lease", (NOW+timedelta(days=2)).isoformat(),
                NOW.isoformat(), NOW.isoformat(), row["id"], row["generation"]))
            claimed = dict(storage.task_by_id(database, (row["id"],)))
        original_import = builtins.__import__
        def without_games(name, *args, **kwargs):
            if name == "app.games" or name.startswith("app.games."):
                raise AssertionError("Study maintenance imported optional Games")
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", without_games)
        # A stale operation never reaches the receiving/admission capability.
        stale = {**claimed, "lease_token": "stale"}
        assert execute_feedback_sweep(stale, read=case.transaction, write=case.transaction,
            capture=lambda *args: capture, clock=lambda: NOW) is False
        assert case.readback() == before
        assert execute_feedback_sweep(claimed, read=case.transaction, write=case.transaction,
            capture=lambda *args: capture, clock=lambda: NOW) is False
        with case.transaction() as database:
            effect = PostgresGameFeedbackStore(database).get(command.key)
            assert effect.status == "applied" and effect.admission_status == "available"
            assert effect.encounter == command and effect.disposition == "first_introduction"
            current_task = dict(storage.task_by_id(database, (task["id"],)))
            assert current_task["state"] == "queued" and json.loads(current_task["payload_json"])["after"]
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        admitted = case.readback()
        assert execute_feedback_sweep(claimed, read=case.transaction, write=case.transaction,
            capture=lambda *args: capture, clock=lambda: NOW) is False
        assert case.readback() == admitted


def test_bounded_retained_history_shared_projection_reaches_study_and_keeps_original_events(
    owned_postgres, monkeypatch,
):
    from threading import Event
    from app.product_composition.encounters import (
        prepare_captured_encounters, receive_admitted_encounter, importance_from_encounters,
    )
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    from test_product_game_lapse import NOW
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    stopped = Event()
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0, study_only=True) as case:
        # The operator-selected window is exactly these two already-retained
        # synthetic IDs. Actual bounded history selection/progress stays #491's.
        prepared = tuple(prepare_captured_encounters(case.capture(game_id), detected_at=NOW.isoformat())
                         for game_id in case.game_ids)
        commands = tuple(command for item in prepared for command in item.encounters)
        report = dict(scanned=len(prepared), matched=len(commands),
            unresolved=sum(len(item.findings)-len(item.encounters) for item in prepared),
            remaining=0, correct=sum(command.observation == "matched" for command in commands),
            missed=sum(command.observation == "missed" for command in commands))
        assert report == dict(scanned=2, matched=2, unresolved=0, remaining=0, correct=1, missed=1)
        def receive(database, item, command):
            return receive_admitted_encounter(database, capture=item.capture, snapshot=item.snapshot,
                command=command, operation_stopped=stopped.is_set,
                clock=lambda: NOW)
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
            receipts = tuple(receive(database, item, command) for item in prepared for command in item.encounters)
            assert [receipt.disposition for receipt in receipts] == ["first_introduction", "first_introduction"]
            assert all(receipt.admission_status == "pending" and receipt.available_by is None for receipt in receipts)
            assert database.execute_native("SELECT introduced_at FROM cards WHERE id=%s", (case.card_id,)).fetchone()[0] is None
        for item in prepared:
            for command in item.encounters:
                assert _study_refresh(case, item.capture, item.snapshot, command).admission_status == "available"
        with case.transaction() as database:
            current, = CurrentOwnerDecisions(database, retained_owner_capture=prepared[0].capture,
                snapshot=prepared[0].snapshot).current_encounter(commands[0])
            importance, = importance_from_encounters(workspace_id=commands[0].key.workspace_id,
                account="synthetic", current_approved_decisions=(current,), encounters=commands+commands)
            assert importance.facts.personal_encounters == 2 and len(importance.differing_response_sources) == 1
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
            _ordinary_review(database, case.protected_id, protected, "bounded-protected:"+case.card_id)
            entry = _issue(database, case.card_id)
            assert _ordinary_review(database, case.card_id, entry, "bounded-first:"+case.card_id)["persisted"]
        learned = case.readback()
        for original, original_receipt in zip(commands, receipts):
            recaptured = prepare_captured_encounters(case.capture(original.key.game_id), detected_at=NOW.isoformat())
            assert len(recaptured.encounters) == 1
            recomparison = recaptured.encounters[0]
            assert recomparison.key == original.key and recomparison.source == original.source
            with case.transaction() as database:
                replay = receive(database, recaptured, recomparison)
                assert replay.owner_receipt_id == original_receipt.owner_receipt_id
                assert replay.admission_status in {"settled", "not_requested"}
        assert case.readback() == learned
