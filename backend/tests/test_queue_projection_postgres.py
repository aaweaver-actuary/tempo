"""Interactive queue read profile on the existing owned PostgreSQL fixture."""
import uuid

import chess
import pytest

from app.services.real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters import frozen_order, postgres_store
from tempo_legacy_database.adapters.http_composition import queue_projection
from test_product_game_lapse_postgres import owned_postgres


@pytest.mark.parametrize('read_name', ['queue_rows', 'available_count'])
def test_interactive_queue_read_preserves_protection_and_scopes_jit_to_transaction(
        owned_postgres, monkeypatch, read_name):
    """A compiled plan must not spend interactive time on unchanged eligibility."""
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    day = '2026-10-30'
    repertoire = 'queue-read-' + uuid.uuid4().hex
    names = ('new', 'held-legacy', 'issued-legacy', 'explicit-legacy',
             'stale-unstarted', 'stale-issued', 'reviewed-legacy', 'guided-legacy')
    cards = [repertoire + '-' + name for name in names]
    with owned_postgres() as database:
        original_jit = database.execute_native("SHOW jit").fetchone()[0]
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Queue proof','synthetic',%s)",
                                (repertoire, day))
        for position, card_id in enumerate(cards):
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,
                state,due_date,content_type,trained_color) VALUES(%s,%s,'prefix',%s,'["e2e4"]',
                'learning',%s,'opening','white')""", (card_id, repertoire, chess.STARTING_FEN, day))
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',
                                    (repertoire, card_id))
            database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
                VALUES(%s,%s,%s,%s)""", (day, card_id, position,
                    'explicit' if names[position] == 'explicit-legacy' else 'new'))
        ranks = [dict(row, rank=position, fingerprint='synthetic', memberships=[repertoire])
                 for position, row in enumerate(database.execute_native(
                     'SELECT * FROM cards WHERE repertoire_id=%s ORDER BY id', (repertoire,)), 1)]
        plan = dict(run_id=repertoire, checksum=repertoire, ranks=ranks)
        frozen_order.stage_run(database, plan, day, reader_version='queue-read-proof')
        frozen_order.stage_ranks(database, plan, ranks)
        frozen_order.activate_run(database, plan)
        database.execute_native('UPDATE queue_attempt_origins SET legacy=1 WHERE card_id=ANY(%s)',
                                ([cards[index] for index in (1, 2, 3, 6, 7)],))
        database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id=ANY(%s)',
                                ([cards[index] for index in (2, 5)],))
        database.execute_native('UPDATE cards SET revision=revision+1 WHERE id=ANY(%s)',
                                ([cards[index] for index in (4, 5)],))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,
            previous_interval,next_interval,source_kind) VALUES(%s,'correct',%s,0,1,'study')""",
                                (cards[6], day + 'T00:00:00Z'))
        database.execute_native("UPDATE daily_queue SET attempt_state='guided' WHERE card_id=%s", (cards[7],))
    try:
        with owned_postgres() as database:
            # Force the compiler admission boundary independently of this tiny
            # fixture's planner estimates. The separate representative paired
            # proof records natural-threshold compilation and timing.
            database.execute_native("SELECT set_config('jit', 'on', true), set_config('jit_above_cost', '0', true)")
            assert database.execute_native('SHOW jit').fetchone()[0] == 'on'
            txid = database.execute_native('SELECT txid_current()').fetchone()[0]
            if read_name == 'queue_rows':
                result = queue_projection.queue_rows(database, day, 20, miss_reason=MISS_REASON)
                assert [row['id'] for row in result] == [cards[index] for index in (0, 2, 3, 5, 6, 7)]
            else:
                assert queue_projection.available_count(database, day, miss_reason=MISS_REASON) == 6
            assert database.execute_native('SHOW jit').fetchone()[0] == 'off'
            assert database.execute_native('SELECT txid_current()').fetchone()[0] == txid
        with owned_postgres() as database:
            assert database.execute_native('SHOW jit').fetchone()[0] == original_jit
            assert database.execute_native('SELECT count(*) FROM reviews WHERE card_id=ANY(%s)',
                                           (cards,)).fetchone()[0] == 1
    finally:
        with owned_postgres() as database:
            database.execute_native('UPDATE frozen_opening_activation SET run_id=NULL WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM frozen_opening_ranks WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM frozen_opening_runs WHERE run_id=%s', (repertoire,))
            database.execute_native('DELETE FROM cards WHERE id=ANY(%s)', (cards,))
            database.execute_native('DELETE FROM repertoires WHERE id=%s', (repertoire,))


def test_optional_encounter_sql_failure_closes_separate_read_and_preserves_core_transaction(owned_postgres, monkeypatch):
    """An actual PostgreSQL error cannot poison Study's separate core read."""
    import psycopg
    def broken(database, *_args):
        return database.execute_native('SELECT * FROM tempo_b_absent_optional_projection').fetchall()
    monkeypatch.setattr(queue_projection, 'encounter_rows', broken)
    with owned_postgres() as core:
        xid = core.execute_native('SELECT txid_current()').fetchone()[0]
        with pytest.raises(psycopg.errors.UndefinedTable):
            with owned_postgres() as optional:
                queue_projection.encounter_badges(optional, [{'start_fen': chess.STARTING_FEN}], '2026-01-01')
        assert core.execute_native('SELECT txid_current()').fetchone()[0] == xid
        assert core.execute_native('SELECT 1').fetchone()[0] == 1


def test_accepted_authority_hold_does_not_become_issuance_blocker_when_retry_expires(case):
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone
    from test_product_game_lapse import qualification_for
    from test_product_game_lapse_postgres import _qualified_policy_owner
    from tempo_legacy_database.adapters.study.game_feedback import (
        PostgresGameFeedbackStore, pending_feedback_preparation, next_feedback_preparation)
    qualification = qualification_for(case.lapse)
    with case.transaction() as database:
        _qualified_policy_owner(case, database, qualification).accept_qualified(case.lapse, qualification=qualification)
    with case.transaction() as database:
        store = PostgresGameFeedbackStore(database)
        accepted = store.get(case.lapse.key)
        assert accepted is not None and accepted.qualification is not None
        assert pending_feedback_preparation(database, '2026-10-30')
        for reason, deferred in (('first_departure_qualification_unavailable', 'authority_unavailable'),
                                 ('later_review_clock', None),
                                 ('encounter_introduction_policy_pending', None)):
            store.save(replace(accepted, reason=reason, deferred_reason=deferred,
                preparation_retry_at=datetime.now(timezone.utc)-timedelta(seconds=1)),
                datetime.now(timezone.utc))
            assert not pending_feedback_preparation(database, '2026-10-30')
            assert next_feedback_preparation(database, '2026-10-30').key == accepted.key
        store.save(replace(accepted, deferred_reason=None, preparation_retry_at=None), datetime.now(timezone.utc))
        assert pending_feedback_preparation(database, '2026-10-30')


from test_product_game_lapse_postgres import case


def test_native_packaged_seed_progresses_through_real_queue_owner(case, monkeypatch):
    """Diagnostic for #668's initial empty head; no browser/package claim."""
    import json
    from types import SimpleNamespace
    from app import queue_commands
    from app.services import durable_tasks, postgres_queue_refresh, study_mode
    from tempo_legacy_database.adapters.queue_refresh import reads
    from tempo_legacy_database.adapters.queue_refresh.eligibility import _DUE_CARD_ELIGIBILITY
    from test_product_game_lapse_postgres import retained_owner_case

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    with case.transaction() as database:
        database.execute_native("UPDATE settings SET timezone='UTC',new_cards_per_day=0,study_only_mode=1 WHERE id=1")
    retained_owner_case(case, False)
    target_id = 'native-packaged-target-' + uuid.uuid4().hex
    with case.transaction() as database:
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,'locked',CURRENT_DATE,'opening'
            FROM cards WHERE id=%s""", (target_id,case.card_id))
        database.execute_native("""UPDATE cards SET due_date=CURRENT_DATE,
            introduced_at=split_part(introduced_at,'T',1) WHERE id=%s""", (case.card_id,))
    target_case = SimpleNamespace(transaction=case.transaction,card_id=target_id,
        decisions=SimpleNamespace(mappings=(case.decisions.mappings[0].model_copy(update={'presentation_id':target_id}),)),
        lapse=case.lapse.model_copy(update={'key':case.lapse.key.model_copy(update={'game_id':uuid.uuid4().hex}),
            'finding':case.lapse.finding.model_copy(update={'presentation_id':target_id})}))
    retained_owner_case(target_case, True)
    with case.transaction() as database:
        database.execute_native("""UPDATE cards target SET state='mature',due_date=CURRENT_DATE+30,
            interval_days=original.interval_days,stability=original.stability,
            fsrs_card_json=original.fsrs_card_json,first_correct_at=original.first_correct_at,
            introduced_at=split_part(target.introduced_at,'T',1)
            FROM cards original WHERE target.id=%s AND original.id=%s""", (target_id,case.card_id))
        day = queue_commands.current_study_day(database)

    def write(operation, **kwargs):
        with case.transaction() as database:
            return operation(database)
    monkeypatch.setattr(durable_tasks, 'submit_background_write', write)
    monkeypatch.setattr(postgres_queue_refresh, 'connection', lambda **kwargs: case.transaction())
    monkeypatch.setattr(postgres_queue_refresh, 'background_read_connection', case.transaction)
    monkeypatch.setattr(reads, 'background_read_connection', case.transaction)
    monkeypatch.setattr(study_mode, 'read_connection', case.transaction)
    monkeypatch.setattr(study_mode, 'background_read_connection', case.transaction)
    monkeypatch.setattr(postgres_queue_refresh.activity_gate, 'wait_for_foreground', lambda: None)
    observations = []
    def observe(stage):
        with case.transaction() as database:
            clock = dict(database.execute_native("SELECT CURRENT_DATE AS pg_day,current_setting('TimeZone') AS pg_timezone").fetchone())
            cards = [dict(row) for row in database.execute_native("""SELECT id,state,due_date,introduced_at,
                pending_validation,archived FROM cards WHERE id=ANY(%s) ORDER BY id""", ([case.card_id,target_id],))]
            eligible = [row[0] for row in database.execute_native('SELECT c.id FROM cards c WHERE ' + _DUE_CARD_ELIGIBILITY,
                (day,day,day))]
            queue = [dict(row) for row in database.execute_native('SELECT * FROM daily_queue ORDER BY id LIMIT 20')]
            projection = [dict(row) for row in database.execute_native('SELECT * FROM queue_projections ORDER BY queue_date LIMIT 5')]
            tasks = [dict(row) for row in database.execute_native("""SELECT id,generation,state,phase,payload_json,last_error
                FROM background_tasks WHERE kind='daily_queue' LIMIT 5""")]
            served = [dict(row) for row in queue_projection.queue_rows(database,day,20,miss_reason=MISS_REASON)]
        value = dict(stage=stage,clock=clock,study_day=day,cards=cards,eligible=eligible,
            queue=queue,projection=projection,tasks=tasks,served=served)
        observations.append(value)
        print('native_seed_probe '+json.dumps(value,default=str),flush=True)
        return value
    before = observe('authored_setup')
    with case.transaction() as database:
        queue_commands.request_queue_refresh_in_transaction(database,day)
    try:
        for index in range(64):
            task = durable_tasks.claim_task('daily_queue')
            if task is None:
                break
            phase = task['payload'].get('_queue_phase','unlock_opening')
            more = postgres_queue_refresh.execute_postgres_queue_refresh_slice(task)
            observe(str(index)+':'+phase)
            if not more:
                break
        final = observe('after_owner')
        assert case.card_id in before['eligible'], 'Authored packaged head is not due/eligible'
        assert any(row['id']==case.card_id for row in final['served']), 'Actual owner did not serve authored protected head'
    finally:
        from pathlib import Path
        path = Path(__file__).resolve().parents[2]/'test-results/collection-release/native-initial-queue-probe.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(observations,indent=2,default=str))
