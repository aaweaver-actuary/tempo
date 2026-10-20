"""Finite command and feedback-store HTTP/Celery/Redis/PostgreSQL boundary proof.

Never runs in regular source tests. Native fixture setup fails closed without
the separately granted owned helper manifest. No mocks or eager Celery mode.
"""
import json

import pytest
from psycopg import sql
from tempo_a_bridge.legacy_study import LegacyStudyOperator
from tempo_game_evidence_contracts import v3


def accepted(native, operator, preview):
    first = operator.accept(preview)
    if not first['accepted']: native.completed(first['operation_id'])
    receipt = operator.receipt(preview)
    assert receipt['accepted'] and not receipt['admission_requested']
    return receipt


def review(native, card, attempt):
    body = dict(outcome='correct', guided=False, queue_entry_id=card['queue_entry_id'],
                expected_revision=card['revision'], attempt_id=attempt)
    return native.post('/api/cards/' + card['id'] + '/review', body, attempt + '-transport'), body


def status(native, operator, preview):
    value = operator.status(preview)
    assert value['workspace_id'] == 'legacy' and value['card_id'] == native.identities['physical']
    return value


def write_trace(native, **value):
    document = dict(native_boundaries=['real loopback HTTP', 'separate foreground/background Celery processes',
        'owned Redis broker/result/admission', 'real module PostgreSQL SQL and backend transactions'],
        synthetic_graph_integrity_owner_facts=True, case_id=native.case_id, counts=native.counts(),
        calls=native.calls, **value)
    (native.directory / 'native-trace.json').write_text(json.dumps(document, indent=2, default=str))


def test_native_generated_accept_replay_restart_and_normal_study(native):
    preview = native.preview()
    operator = LegacyStudyOperator(native.endpoint, native.request)
    assert not status(native, operator, preview)['accepted']
    assert native.counts()['frontier'] == 0
    with native.connection() as raw:
        ordinary = raw.execute('SELECT row_to_json(card) FROM cards card WHERE id=%s', (native.identities['ordinary'],)).fetchone()[0]
    receipt = accepted(native, operator, preview)
    # Discard the caller's first response, restart a real command process, and
    # recover through the durable operation receipt and the identical POST.
    native.stop('background'); native.worker('background')
    assert operator.receipt(preview) == receipt
    assert accepted(native, operator, preview) == receipt
    delivery = v3.EvidenceDelivery.model_validate(preview['delivery'])
    transport = delivery.model_copy(update={'delivery_id': delivery.delivery_id + ':fresh-transport'})
    second = native.post('/api/game-evidence/legacy/deliveries', transport.model_dump(mode='json'), native.case_id + '-new-transport')
    assert second['status'] == 'completed' and second['learning_revision'] == receipt['acknowledgement']['learning_revision']
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == counts['remediation_incident_versions'] == counts['remediation_evidence'] == 1
    assert counts['remediation_deliveries'] == 2 and counts['remediation_attempts'] == counts['target_reviews'] == 0
    admitted = operator.queue(preview, native.case_id + '-queue')
    result = admitted['response']
    if result.get('state') != 'complete' and 'operation_id' in result: result = native.completed(result['operation_id'])
    assert result['status'] == 'applied'
    current = status(native, operator, preview)
    assert current['accepted'] and current['current'] and current['settlement'] == 'unsettled'
    assert current['played_move'] == 'g8f6' and current['expected_moves'] == ['a7a6']
    assert current['admission']['waiting_behind_protected_head']
    window = native.queue(2)['cards']
    assert [card['id'] for card in window] == [native.identities['protected'], native.identities['physical']]
    assert native.counts()['remediation_attempts'] == 0  # Actual normal prefetch did not issue the target.
    review(native, window[0], native.case_id + '-protected-review')
    target = native.queue(1)['cards'][0]
    assert target['id'] == native.identities['physical'] and target['queue_entry_id'] == result['queue_entry_id']
    with native.connection() as raw:
        bound = raw.execute('SELECT card_id,card_revision,frontier_sequence FROM remediation_attempts').fetchall()
        assert bound == [(target['id'], 1, 1)]
        assert raw.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s AND revision=1', (target['queue_entry_id'],)).fetchone() == (1,)
        assert json.loads(raw.execute('SELECT moves_json FROM cards WHERE id=%s', (target['id'],)).fetchone()[0]) == ['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6']
    attempt = native.case_id + '-target-review'
    saved, body = review(native, target, attempt)
    assert saved['persisted'] and saved['review_id'] is not None
    assert native.post('/api/cards/' + target['id'] + '/review', body, attempt + '-transport') == saved
    assert native.post('/api/cards/' + target['id'] + '/review', body, attempt + '-new-transport')['review_id'] == saved['review_id']
    settled = status(native, operator, preview)
    assert settled['settlement'] == 'settled'
    counts = native.counts()
    assert counts['target_reviews'] == counts['remediation_attempts'] == counts['remediation_review_receipts'] == counts['remediation_settlements'] == counts['frontier'] == 1
    with native.connection() as raw:
        assert raw.execute('SELECT row_to_json(card) FROM cards card WHERE id=%s', (native.identities['ordinary'],)).fetchone()[0] == ordinary
        assert raw.execute('SELECT command_name,background,state FROM operation_receipts WHERE operation_id=%s', (receipt['operation_id'],)).fetchone() == ('game_evidence.receive_legacy', True, 'complete')
        assert raw.execute('SELECT command_name,background,state FROM operation_receipts WHERE operation_id=%s', (native.case_id + '-queue',)).fetchone() == ('game_evidence.admit_legacy', False, 'complete')
        assert raw.execute('SELECT COUNT(*) FROM review_attempt_receipts WHERE card_id=%s', (target['id'],)).fetchone()[0] == 1
    write_trace(native, acknowledgement=receipt, admission=result, status=settled, review_id=saved['review_id'], worker_restarted=True)


@pytest.mark.parametrize('native', ['prior'], indirect=True)
def test_native_prior_issued_attempt_cannot_settle_later_acceptance(native):
    target = native.queue(1)['cards'][0]  # Genuine issuance precedes acceptance.
    assert target['id'] == native.identities['physical']
    preview = native.preview(); operator = LegacyStudyOperator(native.endpoint, native.request)
    receipt = accepted(native, operator, preview)
    queued = operator.queue(preview, native.case_id + '-queue')['response']
    if 'operation_id' in queued: queued = native.completed(queued['operation_id'])
    assert queued['status'] == 'deferred' and queued['reason'] == 'issued_head_preserved'
    saved, _body = review(native, target, native.case_id + '-prior-review')
    assert saved['persisted'] and native.counts()['target_reviews'] == 1
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == 1
    assert counts['remediation_attempts'] == counts['remediation_review_receipts'] == counts['remediation_settlements'] == 0
    assert status(native, operator, preview)['settlement'] == 'unsettled'
    write_trace(native, acknowledgement=receipt, admission=queued, prior_issued=True, review_id=saved['review_id'])


def test_native_module_effect_and_complete_operation_receipt_roll_back_together(native):
    preview = native.preview(); operator = LegacyStudyOperator(native.endpoint, native.request)
    delivery = v3.EvidenceDelivery.model_validate(preview['delivery'])
    operation = operator.accept_operation(delivery)
    assert len(delivery.obligations) == 1
    # Compare every receipt field; the module-owned Learning revision must
    # equal its real workspace/evidence rows below, not a scripted initial value.
    expected = dict(contract_version=3, workspace_id='legacy', delivery_id=delivery.delivery_id,
        evidence_id=delivery.evidence_id, evidence_revision=delivery.evidence_revision, status='completed', reason=None,
        accepted_obligation_ids=[delivery.obligations[0].obligation_id])
    marker = 'bridge-native-complete-fault:' + native.case_id
    with native.connection() as raw:
        raw.execute("""CREATE FUNCTION bridge_native_fail_complete() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE ack jsonb; contribution_count bigint; observed_frontier bigint; accepted_revision bigint;
            BEGIN IF NEW.operation_id=TG_ARGV[0] AND NEW.state='complete' THEN
              ack := NEW.response_json::jsonb;
              IF (ack - 'learning_revision') IS DISTINCT FROM TG_ARGV[1]::jsonb
                 OR jsonb_typeof(ack->'learning_revision') IS DISTINCT FROM 'number' THEN
                RAISE EXCEPTION 'native fault prerequisite: expected completed acceptance response';
              END IF;
              SELECT COUNT(*) INTO contribution_count FROM remediation_contributions
                WHERE workspace_id='legacy' AND card_id=TG_ARGV[2] AND obligation_id=TG_ARGV[3]
                  AND evidence_id=ack->>'evidence_id';
              SELECT sequence INTO observed_frontier FROM remediation_cards
                WHERE workspace_id='legacy' AND card_id=TG_ARGV[2];
              SELECT revision INTO accepted_revision FROM remediation_workspaces WHERE workspace_id='legacy';
              IF contribution_count IS DISTINCT FROM 1 OR observed_frontier IS DISTINCT FROM 1
                 OR accepted_revision IS NULL OR accepted_revision<1
                 OR (ack->>'learning_revision')::bigint IS DISTINCT FROM accepted_revision
                 OR NOT EXISTS(SELECT 1 FROM remediation_evidence WHERE workspace_id='legacy'
                     AND evidence_id=ack->>'evidence_id' AND evidence_revision=(ack->>'evidence_revision')::bigint
                     AND learning_revision=accepted_revision)
                 OR NOT EXISTS(SELECT 1 FROM remediation_deliveries WHERE workspace_id='legacy'
                     AND delivery_id=ack->>'delivery_id' AND response_json=ack) THEN
                RAISE EXCEPTION 'native fault prerequisite: accepted module contribution/frontier/receipt missing';
              END IF;
              RAISE EXCEPTION '%', TG_ARGV[4] USING ERRCODE='40001';
            END IF; RETURN NEW; END $$""")
        arguments = sql.SQL(',').join(map(sql.Literal, [operation, json.dumps(expected),
            delivery.obligations[0].card_id, delivery.obligations[0].obligation_id, marker]))
        raw.execute(sql.SQL("CREATE TRIGGER bridge_native_fail_complete BEFORE UPDATE ON operation_receipts FOR EACH ROW EXECUTE FUNCTION bridge_native_fail_complete({})").format(arguments))
    first = operator.accept(preview)
    assert not first['accepted']
    retry = native.wait(lambda: (receipt if (receipt := native.operation(operation))['state'] == 'retrying' else None))
    assert retry['last_error']['class'] == 'SerializationFailure'
    assert retry['last_error']['message'].splitlines()[0] == marker
    assert retry.get('response') is None
    assert all(count == 0 for count in native.counts().values())
    with native.connection() as raw:
        saved = raw.execute('SELECT command_name,payload_json,response_json,attempt_count FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()
        assert saved[0] == 'game_evidence.receive_legacy' and json.loads(saved[1]) == {'delivery': preview['delivery']}
        assert saved[2] is None and saved[3] >= 1
        raw.execute('DROP TRIGGER bridge_native_fail_complete ON operation_receipts')
        raw.execute('DROP FUNCTION bridge_native_fail_complete()')
    def retry_due():
        with native.connection() as raw:
            return raw.execute('SELECT next_retry_at<=NOW() FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()[0]
    native.wait(retry_due, seconds=10)
    # The production scheduler supplies this maintenance wake. Retain the
    # actual recovery owner rather than relying on another intake POST to
    # supply the scheduler's responsibility for a due durable retry.
    native.recover_operations()
    native.completed(operation)
    receipt = accepted(native, operator, preview)
    counts = native.counts()
    assert counts['frontier'] == counts['remediation_contributions'] == counts['remediation_deliveries'] == counts['remediation_evidence'] == 1
    with native.connection() as raw:
        state, attempts = raw.execute('SELECT state,attempt_count FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()
        assert state == 'complete' and attempts >= 2
    write_trace(native, acknowledgement=receipt, retained_retry=retry, injected_fault_marker=marker,
                rollback_before_retry=True, recovery_task='app.tasks.recover_operations')


def feedback_store(raw):
    from app.postgres_store import PostgresConnection, tempo_row_factory
    from app.storage.real_game_feedback import BorrowedFeedbackStore
    raw.row_factory = tempo_row_factory
    return BorrowedFeedbackStore(PostgresConnection(raw))


def insert_feedback_miss(raw, native, *, repertoire='ruy', suffix='weak', ply=5):
    """Publish synthetic evidence through the owned storage tables, not a view."""
    event_id = native.case_id + '-' + suffix
    raw.execute("""INSERT INTO game_derivation_jobs
        (game_id,status,derivation_version,published_repertoire_version,updated_at)
        SELECT id,'complete',1,1,played_at FROM imported_games WHERE id=%s
        ON CONFLICT(game_id) DO NOTHING""", (native.identities['game'],))
    raw.execute("""INSERT INTO repertoire_decision_events_staged
        (id,game_id,derivation_version,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at)
        SELECT %s,id,1,%s,%s,%s,'synthetic-feedback-root','a7a6','g8f6','miss',played_at,played_at
        FROM imported_games WHERE id=%s""",
        (event_id, native.identities[repertoire], native.identities['physical'], ply, native.identities['game']))
    current = raw.execute('SELECT id FROM current_repertoire_decision_events WHERE id=%s', (event_id,)).fetchone()
    assert current and current[0] == event_id, 'Owned staged fixture must be visible through the current publication'
    return event_id


def test_native_feedback_typed_projection_and_priority_effect_borrow_caller_transaction(native):
    from app.storage.real_game_feedback import BATCH_SIZE
    card_id, game_id = native.identities['physical'], native.identities['game']
    with pytest.raises(RuntimeError, match='caller rollback'):
        with native.connection(role='tempo_writer') as raw:
            store = feedback_store(raw)
            event_id = insert_feedback_miss(raw, native)
            assert [(row.card_id, row.state) for row in store.outstanding_cards((card_id, card_id, 'missing'))] == [(card_id, 'learning')]
            assert store.outstanding_card('missing') is None and store.outstanding_cards(()) == ()
            with pytest.raises(ValueError, match='128'):
                store.outstanding_cards((card_id,) * (BATCH_SIZE + 1))
            assert list(store.missed_event_batches(game_id)) == [(event_id,)]
            evidence = [row for batch in store.repertoire_miss_batches(native.identities['ruy']) for row in batch]
            assert [(row.event_id, row.card_id, row.game_id) for row in evidence] == [(event_id, card_id, game_id)]
            raw.execute("UPDATE daily_queue SET gameplay_priority_reason='contract-original' WHERE card_id=%s", (native.identities['ordinary'],))
            store.replace_miss_priority(native.identities['ordinary'], 'contract-original', 'contract-replacement')
            assert raw.execute('SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s',
                               (native.identities['ordinary'],)).fetchone()[0] == 'contract-replacement'
            with native.connection(role='tempo_reader') as observer:
                assert observer.execute('SELECT COUNT(*) FROM repertoire_decision_events WHERE game_id=%s', (game_id,)).fetchone()[0] == 0
                assert observer.execute('SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s',
                                        (native.identities['ordinary'],)).fetchone()[0] is None
            raise RuntimeError('caller rollback')
    with native.connection(role='tempo_reader') as observer:
        assert observer.execute('SELECT COUNT(*) FROM repertoire_decision_events WHERE game_id=%s', (game_id,)).fetchone()[0] == 0
        assert observer.execute('SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s',
                                (native.identities['ordinary'],)).fetchone()[0] is None
    write_trace(native, feedback_contract='typed projections and caller-owned rollback')


def test_native_feedback_card_and_day_locks_live_until_caller_transaction_ends(native):
    from datetime import date
    from psycopg.errors import LockNotAvailable
    card_id, day = native.identities['physical'], date.today().isoformat()
    card_key, day_key = 'tempo:card-edit:' + card_id, 'tempo:daily-queue-position:' + day
    with native.connection(role='tempo_writer') as raw:
        store = feedback_store(raw)
        store.lock_card(card_id)
        store.lock_queue_day(day)
        with native.connection(role='tempo_writer') as observer:
            for key in (card_key, day_key):
                assert observer.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (key,)).fetchone()[0] is False
            with pytest.raises(LockNotAvailable):
                with observer.transaction():
                    observer.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE NOWAIT', (card_id,)).fetchone()
    with native.connection(role='tempo_writer') as observer:
        for key in (card_key, day_key):
            assert observer.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (key,)).fetchone()[0] is True
        assert observer.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE NOWAIT', (card_id,)).fetchone()[0] == card_id
    write_trace(native, feedback_contract='actual card/day advisory and row locks share caller lifetime')


def test_native_feedback_accepted_scope_exclusion_does_not_hide_other_repertoire(native):
    from datetime import date
    from app.postgres_store import PostgresConnection
    from app.services.queue_admission_adapter import QueueAdmissionAdapter
    from app.services.real_game_feedback import MISS_REASON
    card_id, day = native.identities['physical'], date.today().isoformat()
    with native.connection(role='tempo_writer') as raw:
        store = feedback_store(raw)
        insert_feedback_miss(raw, native)
        assert store.outstanding_card(card_id) is not None
        assert QueueAdmissionAdapter(PostgresConnection(raw)).has_outstanding_miss(card_id)
    preview = native.preview()
    receipt = accepted(native, LegacyStudyOperator(native.endpoint, native.request), preview)
    with native.connection(role='tempo_writer') as raw:
        store = feedback_store(raw)
        queue = QueueAdmissionAdapter(PostgresConnection(raw))
        assert store.outstanding_card(card_id) is None
        assert queue.has_outstanding_miss(card_id) is False
        assert list(store.repertoire_miss_batches(native.identities['ruy'])) == []
        raw.execute('SAVEPOINT combined_queue_projection')
        # Exercise the actual combined queue projection using a temporary new
        # admission state; restore the accepted physical card before leaving.
        raw.execute("UPDATE cards SET state='locked',introduced_at=NULL WHERE id=%s", (card_id,))
        assert queue.opening_candidate_page(day, '').candidates == ()
        raw.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)',
                    (native.identities['italian'], card_id))
        other_event = insert_feedback_miss(raw, native, repertoire='italian', suffix='other-scope', ply=6)
        assert store.outstanding_card(card_id) is not None
        assert queue.has_outstanding_miss(card_id) is True
        candidates = queue.opening_candidate_page(day, '').candidates
        assert candidates and {row['id'] for row in candidates} == {card_id}
        assert all(row['gameplay_priority_reason'] == MISS_REASON for row in candidates)
        assert [row.event_id for batch in store.repertoire_miss_batches(native.identities['italian']) for row in batch] == [other_event]
        raw.execute('UPDATE imported_games SET adaptive_excluded=1 WHERE id=%s', (native.identities['game'],))
        assert store.outstanding_card(card_id) is None and queue.has_outstanding_miss(card_id) is False
        raw.execute('ROLLBACK TO SAVEPOINT combined_queue_projection')
        assert store.outstanding_card(card_id) is None
    write_trace(native, feedback_contract='native accepted scope and combined queue projection with different-repertoire controls',
                acknowledgement=receipt)


def exposure_control(selection, result):
    return {name: selection[name] for name in ('target_id','workspace_id','card_id')} | {'encounter_key': result['encounter_key']}


def exposure_queue(native):
    with native.connection() as raw:
        return raw.execute("SELECT id,card_id,position,status,gameplay_priority_reason FROM daily_queue ORDER BY position,id").fetchall()


def poll_exposure(native, control, state):
    # The real scheduler wake runs in the separate background Celery process.
    def read():
        value=native.exposure('/status',control)
        if value['state']==state:return value
        native.start('recovery','from app.celery_app import celery_app;celery_app.send_task("app.tasks.poll_background_tasks",queue="background")','tempo_writer')
        try:assert native.processes['recovery'].wait(timeout=5)==0
        finally:native.stop('recovery')
        return None
    return native.wait(read)


def test_native_historical_practice_priority_replay_and_actual_normal_completion(native):
    from datetime import datetime,timedelta,timezone,date
    # Historical encounter precedes introduction. All data and owner facts are synthetic.
    with native.connection() as raw:
        raw.execute('UPDATE cards SET introduced_at=%s WHERE id=%s',
            ((datetime.now(timezone.utc)-timedelta(hours=12)).isoformat(),native.identities['physical']))
        raw.execute('UPDATE daily_queue SET position=2 WHERE card_id=%s',(native.identities['ordinary'],))
        recall=native.case_id+'-recall'
        raw.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,introduced_at,content_type) SELECT %s,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,introduced_at,content_type FROM cards WHERE id=%s",(recall,native.identities['ordinary']))
        raw.execute("INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,admission_kind) VALUES(%s,%s,1,'Priority review · missed in a recent game','review')",(date.today().isoformat(),recall))
    selection=native.exposure_selection()
    denied,denial=native.request('POST','/api/learning/historical-exposure',selection,{'Idempotency-Key':native.case_id+'-unauthorized'})
    assert denied==401,denial
    before=exposure_queue(native)
    result=native.exposure('',selection,native.case_id+'-practice')
    assert result['state']=='pending' and result['placement_mode']=='best_effort_front'
    control=exposure_control(selection,result)
    placed=exposure_queue(native)
    assert [row[1] for row in placed]==[native.identities['protected'],recall,native.identities['physical'],native.identities['ordinary']]
    assert placed[0]==before[0] and placed[1]==before[1]
    assert native.exposure('',selection,native.case_id+'-practice')==result
    assert native.exposure('',selection,native.case_id+'-semantic-replay')==result
    assert exposure_queue(native)==placed
    # Genuine queue issuance, normal reviews, and exact receipt replay.
    review(native,native.queue(1)['cards'][0],native.case_id+'-head-review')
    card=native.queue(1)['cards'][0];assert card['id']==recall
    review(native,card,native.case_id+'-recall-review')
    target=native.queue(1)['cards'][0];assert target['id']==native.identities['physical']
    with native.connection() as raw:
        issued=raw.execute('SELECT first_issued_at FROM queue_attempt_origins WHERE queue_entry_id=%s AND revision=1',(target['queue_entry_id'],)).fetchone()[0]
        accepted_at=raw.execute("SELECT record_json->>'accepted_at' FROM historical_exposure_intents").fetchone()[0]
        assert datetime.fromisoformat(issued)>=datetime.fromisoformat(accepted_at)
    assert native.queue(1)['cards'][0]['queue_entry_id']==target['queue_entry_id']
    saved,body=review(native,target,native.case_id+'-practice-review')
    assert saved['persisted']
    assert native.post('/api/cards/'+target['id']+'/review',body,native.case_id+'-review-replay')['review_id']==saved['review_id']
    served=poll_exposure(native,control,'served')
    assert served['completion_review_id']==str(saved['review_id'])
    with native.connection() as raw:
        assert raw.execute('SELECT first_issued_at FROM queue_attempt_origins WHERE queue_entry_id=%s AND revision=1',(target['queue_entry_id'],)).fetchone()[0]==issued
        assert raw.execute('SELECT COUNT(*) FROM historical_exposure_intents').fetchone()[0]==1
    assert native.counts()['target_reviews']==1 and native.counts()['remediation_contributions']==0
    write_trace(native,practice=result,served=served,first_issued_at=issued,priority_replay=True)


@pytest.mark.parametrize('native',['prior'],indirect=True)
def test_native_historical_practice_protected_fallback_old_issuance_and_cancel(native):
    from datetime import datetime,timedelta,timezone
    with native.connection() as raw:
        raw.execute('UPDATE cards SET introduced_at=%s WHERE id=%s',
            ((datetime.now(timezone.utc)-timedelta(hours=12)).isoformat(),native.identities['physical']))
    selection=native.exposure_selection();before=exposure_queue(native)
    result=native.exposure('',selection,native.case_id+'-practice')
    assert result['state']=='pending' and result['placement_mode']=='ordinary_queue'
    assert result['last_admission']['reason']=='existing_protected_or_priority_work'
    assert exposure_queue(native)==before
    control=exposure_control(selection,result);target=native.queue(1)['cards'][0]
    review(native,target,native.case_id+'-old-issued-review')
    # A real pre-request issuance, then unknown old issuance, cannot fulfill it.
    native.exposure('/refresh',control,native.case_id+'-refresh')
    def refreshed():
        with native.connection() as raw:
            row=raw.execute("SELECT state,generation FROM background_tasks WHERE kind='historical_exposure_refresh'").fetchone()
            return row and row[1]>=3
    native.start('recovery','from app.celery_app import celery_app;celery_app.send_task("app.tasks.poll_background_tasks",queue="background")','tempo_writer')
    try:assert native.processes['recovery'].wait(timeout=5)==0
    finally:native.stop('recovery')
    native.wait(refreshed)
    assert native.exposure('/status',control)['state']=='pending'
    from app.postgres_store import PostgresConnection,tempo_row_factory
    from app.storage.historical_exposure import BorrowedExposureStore
    from app.storage.historical_exposure_practice import completed_practices
    from tempo_learning_review.historical_exposure import CardScope
    with native.connection() as raw:
        raw.row_factory=tempo_row_factory;db=PostgresConnection(raw);scope=CardScope(selection['target_id'],'legacy',selection['card_id'])
        records=BorrowedExposureStore(db,scope).pending_for_card(scope)
        assert completed_practices(db,scope,records,datetime.now(timezone.utc))==()
        raw.execute('UPDATE queue_attempt_origins SET first_issued_at=NULL WHERE queue_entry_id=%s',(target['queue_entry_id'],))
        assert completed_practices(db,scope,records,datetime.now(timezone.utc))==()
    cancelled=native.exposure('/cancel',control,native.case_id+'-cancel')
    assert cancelled['state']=='cancelled'
    assert native.exposure('/cancel',control,native.case_id+'-cancel')==cancelled
    assert native.counts()['target_reviews']==1
    write_trace(native,practice=result,cancelled=cancelled,old_and_null_issuance_unserved=True)


def test_native_historical_practice_position_fault_rolls_back_intent_admission_and_receipt(native):
    from datetime import datetime,timedelta,timezone
    with native.connection() as raw:
        raw.execute('UPDATE cards SET introduced_at=%s WHERE id=%s',
            ((datetime.now(timezone.utc)-timedelta(hours=12)).isoformat(),native.identities['physical']))
    selection=native.exposure_selection();before=exposure_queue(native);marker='historical-placement-fault'
    with native.connection() as raw:
        # Fire only after a real position write and after intent + admission exist.
        raw.execute("""CREATE FUNCTION exposure_fail_position() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF OLD.position IS DISTINCT FROM NEW.position THEN
          IF NOT EXISTS(SELECT 1 FROM historical_exposure_intents WHERE card_id=TG_ARGV[0])
             OR NOT EXISTS(SELECT 1 FROM daily_queue WHERE card_id=TG_ARGV[0]) THEN
             RAISE EXCEPTION 'native placement prerequisite missing'; END IF;
          RAISE EXCEPTION 'historical-placement-fault'; END IF; RETURN NEW; END $$""")
        raw.execute(sql.SQL('CREATE TRIGGER exposure_fail_position AFTER UPDATE OF position ON daily_queue FOR EACH ROW EXECUTE FUNCTION exposure_fail_position({})').format(sql.Literal(native.identities['physical'])))
    operation=native.case_id+'-fault'
    code,response=native.request('POST','/api/learning/historical-exposure',selection,
        {'Authorization':'Bearer '+(native.private/'exposure-token').read_text(),'Idempotency-Key':operation})
    assert code in (200,202),(code,response)
    failed=native.wait(lambda:(row if (row:=native.operation(operation))['state']=='failed' else None))
    assert marker in str(failed.get('error',failed))
    assert exposure_queue(native)==before
    with native.connection() as raw:
        assert raw.execute('SELECT COUNT(*) FROM historical_exposure_intents').fetchone()[0]==0
        assert raw.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(native.identities['physical'],)).fetchone()[0]==0
        assert raw.execute('SELECT response_json FROM operation_receipts WHERE operation_id=%s',(operation,)).fetchone()[0] is None
        raw.execute('DROP TRIGGER exposure_fail_position ON daily_queue');raw.execute('DROP FUNCTION exposure_fail_position()')
    result=native.exposure('',selection,native.case_id+'-recovered')
    control=exposure_control(selection,result)
    with native.connection() as raw:
        raw.execute("UPDATE background_tasks SET state='failed',last_error='synthetic terminal retry' WHERE kind='historical_exposure_refresh'")
    status=native.exposure('/status',control)
    assert status['state']=='pending' and status['retry_required']
    refresh=native.exposure('/refresh',control,native.case_id+'-manual-retry')
    assert refresh['refresh_requested']
    with native.connection() as raw:
        assert raw.execute("SELECT state FROM background_tasks WHERE kind='historical_exposure_refresh'").fetchone()[0]=='queued'
    assert native.counts()['target_reviews']==0
    write_trace(native,rollback_failure=failed,recovered=result,visible_failed_pending=status,manual_retry=refresh)
