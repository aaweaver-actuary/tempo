"""Intake/Study refresh isolation on a synthetic retained-history workload.

No source games, identifiers, FENs or private payloads are copied from installation.
The structural profile is731 mixed queued cards,4197 reviews, null classifications,
13 repertoires,62 approved route aliases, one current and24 historical issued heads.
"""
import builtins
from contextlib import contextmanager
from datetime import timedelta
import json
import time

import pytest

from test_product_game_lapse_postgres import owned_postgres,case,NOW
from test_scaled_game_priority_postgres import scaled_queue
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    current_owner_encounter_intake,current_owner_encounter_feedback,
)
from app.study.encounter_feedback import EncounterIntroductionPolicy
from app.product_composition.encounters.receiver import receive_captured_encounter


@contextmanager
def representative(case):
    capture,_,command=scaled_queue(case,731)
    repertoire=command.decision.repertoire_id
    with case.transaction() as database:
        for number in range(1,13):
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Synthetic','fixture',%s)",
                (repertoire+':'+str(number),NOW.isoformat()))
        database.execute_native("""UPDATE cards SET content_type=CASE WHEN split_part(id,':',2)::int<427
            THEN 'opening' WHEN split_part(id,':',2)::int<724 THEN 'tactic' ELSE 'defense' END,
            state=CASE WHEN split_part(id,':',2)::int BETWEEN 394 AND 426 OR split_part(id,':',2)::int BETWEEN 687 AND 723
            THEN 'mature' ELSE 'learning' END,canonical_route_source=0
            WHERE id LIKE %s""",(case.card_id+':%',))
        database.execute_native("UPDATE cards SET state='learning',moves_json='[\"e2e4\",\"e7e5\"]',canonical_route_source=1 WHERE id=%s",(case.card_id,))
        database.execute_native("UPDATE repertoire_lines SET moves_json='[\"e2e4\",\"e7e5\"]' WHERE repertoire_id=%s",(repertoire,))
        database.execute_native("UPDATE opening_graph_steps SET moves_json='[\"e2e4\",\"e7e5\"]' WHERE repertoire_id=%s",(repertoire,))
        route=capture['tables']['repertoire_lines'][0]['id']
        for number in range(1,62):
            alias=route+':alias:'+str(number)
            database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                SELECT %s,repertoire_id,name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE id=%s""",(alias,route))
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,
                decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json)
                SELECT repertoire_id,generation,%s,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,
                segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE line_id=%s""",(alias,route))
        day=NOW.date().isoformat()
        database.execute_native('UPDATE daily_queue SET card_bucket=NULL,admission_kind=NULL WHERE queue_date=%s',(day,))
        database.execute_native("UPDATE daily_queue SET card_bucket='tactic',admission_kind='review',attempt_state='reinforcement',cycle=2 WHERE card_id=%s AND queue_date=%s",(case.card_id+':723',day))
        database.execute_native('UPDATE daily_queue SET position=730 WHERE card_id=%s AND queue_date=%s',(case.card_id+':712',day))
        database.execute_native('UPDATE daily_queue SET position=712 WHERE card_id=%s AND queue_date=%s',(case.card_id,day))
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=0 WHERE queue_date=%s AND card_id<>%s',(day,case.card_id+':0'))
        old_day=(NOW-timedelta(days=1)).date().isoformat()
        database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
            SELECT %s,%s||':'||n,n,'review' FROM generate_series(1,24) n""",(old_day,case.card_id))
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_date=%s',(old_day,))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref)
            SELECT %s||':'||n,'correct',%s,10,20,'good',0,'study',%s||':history:'||n||':'||r
            FROM generate_series(0,638) n CROSS JOIN generate_series(1,6) r""",(case.card_id,NOW.isoformat(),case.card_id))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref)
            SELECT %s||':'||n,'correct',%s,10,20,'good',0,'study',%s||':extra:'||n FROM generate_series(0,353) n""",
            (case.card_id,NOW.isoformat(),case.card_id))
        database.execute_native("""INSERT INTO review_attempt_receipts(attempt_id,card_id,outcome,guided,completed_at,review_id,scheduling_status,result_json)
            SELECT %s||':receipt:'||id,card_id,'correct',0,reviewed_at,id,'applied','{}' FROM reviews WHERE card_id=%s""",(case.card_id,case.card_id))
        database.execute_native('UPDATE settings SET study_only_mode=1,new_cards_per_day=0,include_defensive_cards_in_daily_stack=1 WHERE id=1')
    from app.legacy_game_observation import capture_game
    from app.repertoire import project_capture
    from app.services.postgres_integrity import current_integrity_certificate
    from tempo_legacy_database.adapters.games.authority.headers import repertoire_metadata
    with case.transaction() as database:
        def reader(sql,params=()):return [dict(row) for row in database.execute_native(sql,params).fetchall()]
        def metadata(identity):
            header=repertoire_metadata(reader,identity)[0]
            cert=current_integrity_certificate(database,identity,header['graph_generation'])
            return dict(header,integrity_current=cert is not None,integrity_certificate=cert)
        capture=capture_game(repertoire,command.key.game_id,(0,),reader=reader,metadata=metadata,now=NOW)
    snapshot=project_capture(capture)
    assert len(snapshot.decision_presentations)==62
    from tempo_game_evidence_contracts.legacy import fingerprint
    command=command.model_copy(update={'decision':min(snapshot.decision_presentations,key=lambda item:item.route_id),
        'source':command.source.model_copy(update={'source_revision':1,'source_digest':fingerprint(capture['game'])})})
    try:yield capture,snapshot,command
    finally:
        with case.transaction() as database:
            database.execute_native('DELETE FROM study_game_feedback WHERE card_id=%s',(case.card_id,))
            database.execute_native('DELETE FROM daily_queue WHERE card_id=%s OR card_id LIKE %s',(case.card_id,case.card_id+':%'))
            database.execute_native('DELETE FROM cards WHERE id LIKE %s',(case.card_id+':%',))


def state(database,case):
    return dict(card=tuple(database.execute_native('SELECT * FROM cards WHERE id=%s',(case.card_id,)).fetchone()),
        reviews=[tuple(r) for r in database.execute_native('SELECT * FROM reviews WHERE card_id=%s ORDER BY id',(case.card_id,)).fetchall()],
        receipts=[tuple(r) for r in database.execute_native('SELECT * FROM review_attempt_receipts WHERE card_id=%s ORDER BY attempt_id',(case.card_id,)).fetchall()],
        protected=[tuple(r) for r in database.execute_native("""SELECT row_to_json(q)::text,row_to_json(o)::text
            FROM queue_attempt_origins o JOIN daily_queue q ON q.id=o.queue_entry_id
            WHERE o.card_id LIKE %s AND o.issued_as_head=1 ORDER BY q.id""",(case.card_id+':%',)).fetchall()])


def test_intake_never_calls_queue_and_commits_once_when_refresh_fails(case,monkeypatch):
    from app.services import queue_priority_promotion,queue_presentation,review_service,queue_application
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(postgres_store,'configured',lambda:True)
    with representative(case) as (capture,snapshot,command):
        with case.transaction() as database:before=state(database,case)
        def forbidden(*args,**kwargs):raise RuntimeError('queue processing unavailable')
        with monkeypatch.context() as guard:
            guard.setattr(queue_priority_promotion,'prepare_priority_promotion',forbidden)
            guard.setattr(queue_presentation,'presentation_rows',forbidden)
            guard.setattr(review_service,'presentation_rows',forbidden)
            guard.setattr(review_service,'ensure_card_queued_after',forbidden)
            guard.setattr(queue_application,'request_queue_refresh_in_transaction',forbidden)
            guard.setattr(PostgresGameFeedbackStore,'promote_encounter',forbidden)
            guard.setattr(PostgresGameFeedbackStore,'lock_target',forbidden)
            with case.transaction() as database:
                database.execute_native("SET LOCAL transaction_timeout='250ms'")
                started=time.perf_counter()
                accepted=receive_captured_encounter(database,capture=capture,snapshot=snapshot,command=command,clock=lambda:NOW)
                print('INTAKE_TOTAL_MS',round((time.perf_counter()-started)*1000,2))
                assert accepted.admission_status=='pending' and accepted.observation_recorded
                assert accepted.reason=='study_priority_pending' and accepted.available_by is None
            with case.transaction() as database:
                duplicate=receive_captured_encounter(database,capture=capture,snapshot=snapshot,command=command,clock=lambda:NOW)
                assert duplicate.outcome=='duplicate' and duplicate.owner_receipt_id==accepted.owner_receipt_id
                assert state(database,case)==before
        with monkeypatch.context() as guard:
            guard.setattr(PostgresGameFeedbackStore,'promote_encounter',forbidden)
            with pytest.raises(RuntimeError,match='queue processing unavailable'):
                with case.transaction() as database:
                    current_owner_encounter_feedback(database,retained_owner_capture=capture,snapshot=snapshot,
                        clock=lambda:NOW,introductions=None,introduction_policy=None).refresh_encounter(command.key)
        # A stale Study-only ordering plan cannot roll back committed intake.
        from app.services.queue_priority_promotion import prepare_priority_promotion,StalePriorityPromotion
        plan=prepare_priority_promotion(NOW.date().isoformat(),case.card_id,read=case.transaction)
        cursor=case.card_id+':stale-refresh'
        with case.transaction() as database:
            database.execute_native('UPDATE daily_queue SET position=9999 WHERE card_id=%s AND queue_date=%s',
                (case.card_id+':26',NOW.date().isoformat()))
            database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,payload_json,
                next_attempt_at,created_at,updated_at) VALUES(%s,'study_game_feedback',%s,'{"after":"before"}',%s,%s,%s)""",
                (cursor,cursor,NOW.isoformat(),NOW.isoformat(),NOW.isoformat()))
        with pytest.raises(StalePriorityPromotion,match='priority_presentation_changed'):
            with case.transaction() as database:
                database.execute_native('UPDATE background_tasks SET payload_json=%s WHERE id=%s',('{"after":"changed"}',cursor))
                current_owner_encounter_feedback(database,retained_owner_capture=capture,snapshot=snapshot,
                    clock=lambda:NOW,introductions=None,introduction_policy=None,prepared_priority=plan).refresh_encounter(command.key)
        with case.transaction() as database:
            assert database.execute_native('SELECT payload_json FROM background_tasks WHERE id=%s',(cursor,)).fetchone()[0]=='{"after":"before"}'
        with case.transaction() as database:
            retained=PostgresGameFeedbackStore(database).get(command.key)
            assert retained.status=='pending' and retained.encounter==command
            assert database.execute_native('SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s',(case.card_id,)).fetchone()[0]==1
            assert state(database,case)==before


def test_study_refresh_without_games_consumes_accepted_priority(case,monkeypatch):
    from tempo_legacy_database.adapters import postgres_store
    from app.product_composition.feedback_maintenance import KIND,execute_feedback_sweep
    from app.services import durable_tasks
    from tempo_legacy_database.adapters.durable_tasks import storage
    monkeypatch.setattr(postgres_store,'configured',lambda:True)
    monkeypatch.setattr(durable_tasks,'_now',lambda:NOW)
    with representative(case) as (capture,snapshot,command):
        with case.transaction() as database:
            before=state(database,case)
            accepted=receive_captured_encounter(database,capture=capture,snapshot=snapshot,command=command,clock=lambda:NOW)
            task=durable_tasks.enqueue_task_in_transaction(database,KIND,case.card_id,{'after':''})
            storage.claim_lease(database,('study-refresh-lease',(NOW+timedelta(days=2)).isoformat(),
                NOW.isoformat(),NOW.isoformat(),task['id'],task['generation']))
            claimed=dict(storage.task_by_id(database,(task['id'],)))
        original=builtins.__import__
        def no_games(name,*args,**kwargs):
            if name=='app.games' or name.startswith('app.games.'):
                raise AssertionError('Study refresh loaded optional Games producer')
            return original(name,*args,**kwargs)
        monkeypatch.setattr(builtins,'__import__',no_games)
        assert execute_feedback_sweep(claimed,read=case.transaction,write=case.transaction,
            capture=lambda *_:capture,clock=lambda:NOW) is False
        with case.transaction() as database:
            effect=PostgresGameFeedbackStore(database).get(command.key)
            assert effect.status=='applied' and effect.admission_status=='available'
            assert effect.encounter==command and effect.disposition=='review_priority'
            assert current_owner_encounter_intake(database,retained_owner_capture=capture,snapshot=snapshot,
                clock=lambda:NOW).encounter_status(command.key).owner_receipt_id==accepted.owner_receipt_id
            assert state(database,case)==before and len(before['reviews'])==9 and len(before['receipts'])==9 and len(before['protected'])==25
            assert database.execute_native('SELECT position FROM daily_queue WHERE card_id=%s AND queue_date=%s',
                (case.card_id,NOW.date().isoformat())).fetchone()[0]==1


def test_applied_game_priority_allows_bounded_daily_queue_preparation_without_losing_attempts(case, monkeypatch):
    from test_scaled_game_priority_postgres import scale, receiving
    from app.services.queue_priority_promotion import prepare_priority_promotion
    from tempo_legacy_database.adapters.study.game_feedback import owed_feedback_card_ids
    from tempo_legacy_database.adapters import postgres_store

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    # Three entries suffice: two retained protected attempts and one target.
    with scale(case, 3) as (capture, snapshot, command):
        with case.transaction() as database:
            database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=0 WHERE card_id=%s', (case.card_id,))
            accepted = receiving(database, capture, snapshot, None).accept_encounter(command)
            assert accepted.admission_status == 'pending'
            assert owed_feedback_card_ids(database, (case.card_id,)) == {case.card_id}
        prepared = prepare_priority_promotion(NOW.date().isoformat(), case.card_id, read=case.transaction)
        with case.transaction() as database:
            available = receiving(database, capture, snapshot, prepared).refresh_encounter(command.key)
            assert available.admission_status == 'available'
            assert available.owner_receipt_id == accepted.owner_receipt_id
            # A stale label on another fixture card is presentation-only;
            # it does not create an obligation or a review.
            database.execute_native("UPDATE daily_queue SET gameplay_priority_reason='product_game_lapse' WHERE card_id=%s",
                (case.card_id+':1',))
            assert owed_feedback_card_ids(database, (case.card_id, case.card_id, case.card_id+':1')) == {case.card_id}
            assert database.execute_native('SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s',
                (case.card_id,)).fetchone()[0] == 'product_game_encounter'

        def preserved():
            with case.transaction() as database:
                return dict(state=state(database, case),
                    queue=[tuple(row) for row in database.execute_native('SELECT * FROM daily_queue ORDER BY id').fetchall()],
                    origins=[tuple(row) for row in database.execute_native('SELECT * FROM queue_attempt_origins ORDER BY queue_entry_id,card_id,revision').fetchall()],
                    cards=[tuple(row) for row in database.execute_native('SELECT * FROM cards ORDER BY id').fetchall()],
                    effects=[tuple(row) for row in database.execute_native('SELECT * FROM study_game_feedback ORDER BY effect_key').fetchall()],
                    review_count=database.execute_native('SELECT COUNT(*) FROM reviews').fetchone()[0],
                    receipt_count=database.execute_native('SELECT COUNT(*) FROM review_attempt_receipts').fetchone()[0])

        before = preserved()
        assert before['state']['protected']
        original_import = builtins.__import__
        def no_games(name, *args, **kwargs):
            if name == 'app.games' or name.startswith('app.games.'):
                raise AssertionError('Queue preparation imported optional Games')
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', no_games)
        from app.services import postgres_queue_refresh
        from tempo_legacy_database.adapters.queue_refresh import reads
        monkeypatch.setattr(postgres_queue_refresh, 'background_read_connection', case.transaction)
        bounded_calls = []
        def bounded(statement, parameters=(), *, native=False):
            assert statement.lstrip().startswith(('SELECT', 'WITH'))
            bounded_calls.append((statement, parameters, native))
            with case.transaction() as database:
                execute = database.execute_native if native else database.execute
                return execute(statement, parameters).fetchall()
        monkeypatch.setattr(reads, '_bounded_read', bounded)
        plans = [postgres_queue_refresh._prepare_queue_randomization(NOW.date().isoformat()) for _ in range(2)]
        # Read IDs explicitly rather than depending on the storage column order.
        with case.transaction() as database:
            expected_ids = {row[0] for row in database.execute_native('SELECT id FROM daily_queue WHERE queue_date=%s AND status=\'queued\'',
                (NOW.date().isoformat(),)).fetchall()}
            original = PostgresGameFeedbackStore(database).get(command.key)
            assert original.status == 'applied' and original.encounter == command
        for plan in plans:
            assert all(isinstance(plan[key], str) and plan[key] for key in ('presentation_state', 'membership_hash', 'raw_membership_hash'))
            assert isinstance(plan['frozen_token'], list)
            assert plan['entries'] is not None and {entry['id'] for entry in plan['entries']} == expected_ids
            assert all(set(entry) == {'id', 'position', 'bucket', 'kind'} for entry in plan['entries'])
        assert plans[0]['presentation_state'] == plans[1]['presentation_state']
        owed_calls = [(sql, parameters, native) for sql, parameters, native in bounded_calls if 'FROM study_game_feedback' in sql]
        assert len(owed_calls) == 2  # one lookup per three-entry priority page, twice
        assert all('?' in sql and not native and len(parameters) == 2 for sql, parameters, native in owed_calls)
        assert preserved() == before
        with case.transaction() as database:
            replay = receiving(database, capture, snapshot, None).accept_encounter(command)
            assert replay.owner_receipt_id == available.owner_receipt_id and replay.outcome == 'duplicate'
            assert replay.admission_status == 'available'
        assert preserved() == before
