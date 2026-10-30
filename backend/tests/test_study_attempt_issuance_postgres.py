"""#622 actual committed issuance/fence proof on the existing owned PG fixture."""
from datetime import timedelta

import pytest

from test_product_game_lapse_postgres import owned_postgres, case, NOW
from tempo_legacy_database.adapters.study.issuance import (
    attempt_issuance, first_target_issuance, issuance_frontier, stamp_issued_head,
)


def qualified_command(case, clock):
    """Typed owner-port fixture; global source validation belongs to #620/#624."""
    from app.contracts.product_v02.learning import GameEncounter
    from app.contracts.product_v02.repertoire import IntroductionGameScope
    from app.study.encounter_feedback import encounter_digest
    from test_product_game_lapse import qualification_for
    mapping = case.decisions.mappings[0].model_copy(update={'route_id':'route-a','route_ply':0})
    command = GameEncounter(key=case.lapse.key,
        source=IntroductionGameScope(provider=case.lapse.key.provider,game_id=case.lapse.key.game_id,
            source_revision=case.lapse.finding.source_revision,source_digest=case.lapse.finding.source_digest),
        decision=mapping,expected_uci=mapping.selected_response_uci,
        played_uci=case.lapse.finding.played_uci,observation='missed',
        detected_at=clock.isoformat(),occurred_at=case.lapse.occurred_at,payload_digest='0'*64)
    command = command.model_copy(update={'payload_digest':encounter_digest(command)})
    qualification = qualification_for(command)
    class Decisions:
        def current_qualified(self, candidate, witness):
            assert candidate == command and witness.matches(candidate)
            return (mapping,)
        def current_encounter(self, candidate):
            assert candidate == command
            return (mapping,)
    return command,qualification,Decisions()


def entry(database, card_id, day, *, position=0):
    return database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,cycle,position)
        VALUES(%s,%s,(SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE card_id=%s),%s)
        RETURNING id""", (day, card_id, card_id, position)).fetchone()[0]


def test_native_committed_issuance_ordinal_survives_rollback_reload_and_rollover(case):
    with case.transaction() as database:
        active = entry(database, case.card_id, NOW.date().isoformat())
        stamp_issued_head(database, active, case.card_id, 1, now=NOW-timedelta(seconds=1))
        old = attempt_issuance(database, active, case.card_id, 1)
    # Original actionable determination precedes acceptance/preparation. Count
    # every actual subsequent new attempt, including an ordinary earlier head.
    determined = NOW.isoformat()
    with case.transaction() as database:
        ordinary = entry(database, case.card_id, NOW.date().isoformat(), position=1)
        stamp_issued_head(database, ordinary, case.card_id, 1, now=NOW+timedelta(seconds=1))
        fence = issuance_frontier(database, lock=True)
    with pytest.raises(RuntimeError, match='rollback issuance'):
        with case.transaction() as database:
            rolled_back = entry(database, case.card_id, NOW.date().isoformat(), position=2)
            stamp_issued_head(database, rolled_back, case.card_id, 1, now=NOW+timedelta(seconds=2))
            raise RuntimeError('rollback issuance')
    with case.transaction() as database:
        target = entry(database, case.card_id, (NOW+timedelta(days=1)).date().isoformat(), position=0)
        stamp_issued_head(database, target, case.card_id, 1, now=NOW+timedelta(days=1))
        first, ordinal = first_target_issuance(database, case.card_id, 1, determined,
                                               after_issuance_sequence=fence)
        assert first.queue_entry_id == target and ordinal == 2
        assert first.issuance_sequence > fence+1  # Sequence gap is not a third card.
        assert first.issuance_sequence > old.issuance_sequence
    with case.transaction() as database:
        before = issuance_frontier(database)
        stamp_issued_head(database, target, case.card_id, 1, now=NOW+timedelta(days=2))
        assert attempt_issuance(database, target, case.card_id, 1) == first
        assert issuance_frontier(database) == before
        assert first_target_issuance(database, case.card_id, 1, determined,
                                     after_issuance_sequence=fence) == (first, 2)


def test_native_legacy_issuance_is_not_backfilled_by_reload(case):
    with case.transaction() as database:
        historical = entry(database, case.card_id, NOW.date().isoformat())
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s',
                                (historical,))
        before = issuance_frontier(database)
        stamp_issued_head(database, historical, case.card_id, 1)
        assert attempt_issuance(database, historical, case.card_id, 1) is None
        assert issuance_frontier(database) == before


def test_native_prepared_priority_moves_unissued_repeat_but_preserves_issued_origin(case):
    from tempo_legacy_database.adapters.study.review.storage import apply_priority_positions, PriorityPositionConflict
    with case.transaction() as database:
        repeated = entry(database, case.card_id, NOW.date().isoformat(), position=20)
        database.execute_native("UPDATE daily_queue SET attempt_state='guided',attempt_failed=1 WHERE id=%s",
                                (repeated,))
        apply_priority_positions(database, NOW.date().isoformat(), ((repeated,20,21),))
        assert tuple(database.execute_native('SELECT position,attempt_failed,attempt_state FROM daily_queue WHERE id=%s',
                                        (repeated,)).fetchone()) == (21,1,'guided')
        stamp_issued_head(database, repeated, case.card_id, 1)
        with pytest.raises(PriorityPositionConflict):
            apply_priority_positions(database, NOW.date().isoformat(), ((repeated,21,22),))


def test_native_pending_reset_saves_current_review_then_issues_urgent_target_first(case, monkeypatch):
    from app import queue_commands
    from app.contracts.transport.study import ReviewRequest
    from app.services import queue_next_card
    from app.services.learning_review_adapter import LearningReviewAdapter, command_from_request
    from app.services.queue_priority_promotion import prepare_priority_promotion
    from app.study.encounter_feedback import EncounterFeedback
    from app.study.interfaces.next_card import NextCardPreparation
    from tempo_learning_review import ReviewPolicy, submit_review
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore, feedback_queue_priorities
    from tempo_legacy_database.adapters import postgres_store
    clock = NOW+timedelta(days=10)
    day = clock.date().isoformat()
    monkeypatch.setattr(queue_commands, 'current_study_day', lambda *_args,**_kwargs: day)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    command,qualification,decisions = qualified_command(case,clock)
    active, ordinary = case.card_id+'-active', case.card_id+'-ordinary'
    with case.transaction() as database:
        for position,card_id in enumerate((active,ordinary)):
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,
                state,due_date,content_type) SELECT %s,repertoire_id,kind,start_fen,moves_json,
                'learning',%s,'tactic' FROM cards WHERE id=%s""", (card_id,day,case.card_id))
            entry(database,card_id,day,position=position)
        ids = {row['card_id']:row['id'] for row in database.execute_native(
            'SELECT id,card_id FROM daily_queue WHERE queue_date=%s',(day,)).fetchall()}
        entry(database,case.card_id,day,position=2)
        stamp_issued_head(database,ids[active],active,1,now=clock-timedelta(seconds=1))
        accepted = EncounterFeedback(PostgresGameFeedbackStore(database),decisions,
            clock=lambda:clock,introductions=None,introduction_policy=None).accept_qualified(command,qualification=qualification)
        assert accepted.admission_status == 'pending' and accepted.progress.scheduling_review_id is None
    request = ReviewRequest(outcome='correct',queue_entry_id=ids[active],expected_revision=1,
        attempt_id=active+'-review',prepared_next=dict(queue_entry_id=ids[ordinary],card_id=ordinary,
            revision=1,cycle=0,local_date=day))
    with case.transaction() as database:
        adapter = LearningReviewAdapter(database,request,observed_at=clock,
            policy_provider=lambda:ReviewPolicy('UTC',7))
        completed = submit_review(adapter,command_from_request(active,request),
            lambda:adapter.context(command_from_request(active,request))).as_dict()
        assert completed['persisted'] and 'prepared_continuation' not in completed
        assert attempt_issuance(database,ids[ordinary],ordinary,1) is None
    # The explicit command's owner capability performs preparation with closed
    # readers before its own writer, independently of an optional Games process.
    def prepare():
        priority = prepare_priority_promotion(day,case.card_id,read=case.transaction,
            priority_order=dict(urgent_determined_at=qualification.determined_at,
                urgent_stream='forward',urgent_effect_id='fixture-effect'))
        class Prepared:
            def apply(self,database):
                available = EncounterFeedback(PostgresGameFeedbackStore(database,prepared_priority=priority),
                    decisions,clock=lambda:clock,introductions=None,introduction_policy=None).refresh_encounter(command.key)
                assert available.admission_status == 'available' and available.progress.scheduling_review_id is not None
                return NextCardPreparation(True)
        return Prepared()
    monkeypatch.setattr(queue_next_card,'_prepare',prepare)
    prepared = queue_next_card.prepare_next_card_command({})
    with case.transaction() as database:
        assert queue_next_card.prepared_next_card_result(database,prepared)['progressed']
    monkeypatch.setattr(queue_commands.queue_storage,'stamp_issued_head',
        lambda database,entry_id,card_id,revision: stamp_issued_head(database,entry_id,card_id,revision,
            now=clock+timedelta(microseconds=1)))
    with case.transaction() as database:
        from test_product_game_lapse import qualification_for
        from tempo_legacy_database.adapters.study.game_feedback import pending_feedback_preparation
        historical = command.model_copy(update={
            'key':command.key.model_copy(update={'game_id':command.key.game_id+'-history'}),
            'source':command.source.model_copy(update={'game_id':command.key.game_id+'-history'}),
            'detected_at':(clock-timedelta(days=3)).isoformat()})
        history_qualification = qualification_for(historical).model_copy(update={'stream':'reconciliation'})
        class HistoricalDecisions:
            def current_qualified(self,candidate,witness):
                assert candidate == historical and witness.matches(candidate)
                return (historical.decision,)
        EncounterFeedback(PostgresGameFeedbackStore(database),HistoricalDecisions(),clock=lambda:clock,
            introductions=None,introduction_policy=None).accept_qualified(historical,qualification=history_qualification)
        # A pending historical occurrence cannot force preparing the entire
        # backlog before this already-ready live head gets actual service.
        assert pending_feedback_preparation(database,day)
        ready = feedback_queue_priorities(database,[case.card_id])[case.card_id]
        assert not pending_feedback_preparation(database,day,before_priority=ready)
        target = database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'",(case.card_id,)).fetchone()[0]
        assert queue_commands.issue_queue_head(database,dict(queue_date=day,entry_id=target,
            card_id=case.card_id,expected_revision=1)) == {'issued':True}
        effect = PostgresGameFeedbackStore(database).get(command.key)
        assert effect.progress.first_issued.queue_entry_id == target
        assert effect.progress.first_issued_ordinal == 1
        assert effect.progress.first_issued.issuance_sequence > effect.progress.remediation_fence.after_issuance_sequence
        assert feedback_queue_priorities(database,[case.card_id])[case.card_id]['urgent_stream'] == 'forward'
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='study'",(active,)).fetchone()[0] == 1
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0] == 1


def test_native_next_card_operation_fence_precedes_all_owner_writes(case,monkeypatch):
    """Real gateway receipts/savepoint; the marker is not fabricated Study history."""
    from app import command_gateway,queue_commands  # noqa: F401 - installs queue handlers for an isolated selection
    from app.services import queue_next_card
    from app.study.interfaces.next_card import NextCardPreparation
    operation = 'next-head-'+case.card_id
    with case.transaction() as database:
        database.execute_native('CREATE TABLE IF NOT EXISTS next_card_fixture_units(case_id TEXT PRIMARY KEY,units BIGINT NOT NULL)')
        database.execute_native('INSERT INTO next_card_fixture_units VALUES(%s,0)',(case.card_id,))
    class Prepared:
        priority = None
        def apply(self,database):
            database.execute_native('UPDATE next_card_fixture_units SET units=units+1 WHERE case_id=%s',(case.card_id,))
            return NextCardPreparation(True)
    prepared_reads = []
    def prepare():
        prepared_reads.append(True)  # Harmless pure preparation can repeat.
        return Prepared()
    monkeypatch.setattr(queue_next_card,'_prepare',prepare)
    monkeypatch.setattr(command_gateway,'_writer_connection',lambda _background:case.transaction())
    first = command_gateway._execute_command(operation,'queue.prepare_next',{})
    assert first['progressed']
    assert command_gateway._execute_command(operation,'queue.prepare_next',{}) == first
    with pytest.raises(command_gateway.CommandConflict):
        command_gateway._execute_command(operation,'queue.prepare_next',{'reused':'different'})
    stale_operation = operation+'-stale'
    execute,saved,token,_ = command_gateway.record_operation_attempt(stale_operation,'queue.prepare_next',{},background=False)
    assert execute and saved == {} and token
    assert command_gateway._execute_command(stale_operation,'queue.prepare_next',{},attempt_token='expired-attempt') is None
    with case.transaction() as database:
        assert database.execute_native('SELECT units FROM next_card_fixture_units WHERE case_id=%s',(case.card_id,)).fetchone()[0] == 1
    assert command_gateway._execute_command(stale_operation,'queue.prepare_next',{},attempt_token=token)['progressed']
    class FailingPrepared(Prepared):
        def apply(self,database):
            super().apply(database)
            raise RuntimeError('owner unit failed after write')
    monkeypatch.setattr(queue_next_card,'_prepare',FailingPrepared)
    assert command_gateway._execute_command(operation+'-rollback','queue.prepare_next',{}) is None
    with case.transaction() as database:
        assert database.execute_native('SELECT units FROM next_card_fixture_units WHERE case_id=%s',(case.card_id,)).fetchone()[0] == 2
        assert database.execute_native('SELECT state FROM operation_receipts WHERE operation_id=%s',(operation+'-rollback',)).fetchone()[0] == 'failed'
    assert len(prepared_reads) == 4  # First, complete replay, stale, current token.
