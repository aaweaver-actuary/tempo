"""A graded failure owns one durable within-session retry decision."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import tempo_learning_review as learning

from app import database
from app.models import ReviewRequest
from app.services.learning_review_adapter import LearningReviewAdapter, command_from_request
from test_queue_attempt_issuance import queued_attempts
from tempo_learning_runtime.retry_store import queue_ready_facts
from app.services.queue_application import available_attempt_sql

FIXED_TIME = datetime(2026, 10, 18, 23, 58, tzinfo=timezone.utc)


def _failed_review(connection, entries, *, reviewed_at=FIXED_TIME, card_id='active-a', token='1'*32):
    request = ReviewRequest(outcome='again', queue_entry_id=entries[card_id],
        attempt_id='readiness-failure-'+card_id, expected_revision=1, recorded_at=reviewed_at.isoformat())
    adapter = LearningReviewAdapter(connection, request, observed_at=reviewed_at,
        retry_draws=lambda: (450, token),
        policy_provider=lambda: SimpleNamespace(timezone='UTC', light_first_interval_days=7))
    command = command_from_request(card_id, request)
    return learning.apply_review(adapter, command, adapter.context(command)).as_dict()


def _facts(connection, *, day='2026-10-18', now=FIXED_TIME, complete=True):
    return queue_ready_facts(connection, day, now,
        base_statement=available_attempt_sql(), complete=complete)


def _complete_projection(connection, day='2026-10-18'):
    connection.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(?,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0",(day,))


def _select(connection, *, now=FIXED_TIME, day='2026-10-18'):
    from app.services.retry_queue import select_queue_head
    return select_queue_head(connection,observed_at=now,local_date=day)


def test_graded_failure_binds_one_retry_to_real_review_and_replay_keeps_deadline(queued_attempts):
    reviewed_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    request = ReviewRequest(outcome="again", guided=False,
        queue_entry_id=queued_attempts["active-a"], expected_revision=1,
        attempt_id="failed-retry-original", recorded_at=reviewed_at.isoformat())
    command = command_from_request("active-a", request)
    with database.connection() as connection:
        adapter = LearningReviewAdapter(connection, request,
            policy_provider=lambda: SimpleNamespace(timezone="UTC", light_first_interval_days=7))
        context = adapter.context(command)
        result = learning.apply_review(adapter, command, context).as_dict()
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='queue_retry_policies'").fetchone()[0] == 1
        policy = dict(connection.execute('SELECT * FROM queue_retry_policies WHERE review_id=?', (result['review_id'],)).fetchone())
        assert policy['retry_queue_entry_id'] == result['requeue_entry_id']
        assert policy['source_queue_entry_id'] == request.queue_entry_id
        assert policy['card_id'] == 'active-a' and policy['revision'] == 1
        assert 300 <= policy['delay_seconds'] <= 600
        assert datetime.fromisoformat(policy['ready_at']) == reviewed_at + timedelta(seconds=policy['delay_seconds'])
        assert policy['released_at'] is None and policy['issued_at'] is None
        assert learning.apply_review(adapter, command, context).as_dict() == result
        assert dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone()) == policy
        assert connection.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', ('active-a',)).fetchone()[0] == 1
        assert connection.execute('SELECT reviewed_at FROM reviews WHERE id=?', (result['review_id'],)).fetchone()[0] == reviewed_at.isoformat()


def test_failed_retry_draw_and_entry_are_once_even_through_direct_adapter_replay(queued_attempts):
    reviewed_at = datetime.now(timezone.utc)
    request = ReviewRequest(outcome='again', queue_entry_id=queued_attempts['active-a'],
        attempt_id='retry-draw-once', expected_revision=1, recorded_at=reviewed_at.isoformat())
    draws = []
    def draw():
        draws.append(True)
        return 450, 'a' * 32
    with database.connection() as connection:
        adapter = LearningReviewAdapter(connection, request, retry_draws=draw,
            policy_provider=lambda: SimpleNamespace(timezone='UTC', light_first_interval_days=7))
        command = command_from_request('active-a', request)
        context = adapter.context(command)
        result = learning.apply_review(adapter, command, context).as_dict()
        source = replace(adapter.find_attempt(command, context), revision=1)
        assert adapter.requeue_failed(learning.FailedRetryRequest(result['review_id'], source, reviewed_at)) == result['requeue_entry_id']
        assert len(draws) == 1
        policy = connection.execute('SELECT * FROM queue_retry_policies').fetchone()
        assert policy['order_token'] == 'a' * 32 and policy['delay_seconds'] == 450
        assert connection.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='active-a'").fetchone()[0] == 2


def test_failure_after_retry_persistence_rolls_back_review_schedule_queue_and_policy(queued_attempts):
    request = ReviewRequest(outcome='again', queue_entry_id=queued_attempts['active-a'],
        attempt_id='retry-rollback', expected_revision=1)
    with database.read_connection() as connection:
        before_card = tuple(connection.execute("SELECT * FROM cards WHERE id='active-a'").fetchone())
        before_queue = [tuple(row) for row in connection.execute('SELECT * FROM daily_queue ORDER BY id')]
    with pytest.raises(RuntimeError, match='after retry persistence'):
        with database.connection() as connection:
            adapter = LearningReviewAdapter(connection, request,
                retry_draws=lambda: (450, 'b' * 32),
                policy_provider=lambda: SimpleNamespace(timezone='UTC', light_first_interval_days=7))
            command = command_from_request('active-a', request)
            def fail_receipt(*_args):
                assert connection.execute('SELECT COUNT(*) FROM queue_retry_policies').fetchone()[0] == 1
                raise RuntimeError('after retry persistence')
            adapter.save_receipt = fail_receipt
            learning.apply_review(adapter, command, adapter.context(command))
    with database.read_connection() as connection:
        assert tuple(connection.execute("SELECT * FROM cards WHERE id='active-a'").fetchone()) == before_card
        assert [tuple(row) for row in connection.execute('SELECT * FROM daily_queue ORDER BY id')] == before_queue
        assert connection.execute('SELECT COUNT(*) FROM queue_retry_policies').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM review_attempt_receipts').fetchone()[0] == 0


def test_cooling_retry_is_excluded_before_unpaged_ready_head_selection(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        connection.execute('UPDATE daily_queue SET position=-1 WHERE id=?', (result['requeue_entry_id'],))
        facts = _facts(connection)
        assert facts.ready_head.entry_id == queued_attempts['later-b']
        assert facts.due_retry is None and facts.cooling_retry is None
        assert learning.choose_queue_action(facts, observed_at=FIXED_TIME).kind == 'issue'


def test_all_cooling_absence_is_not_derived_from_a_page_or_incomplete_projection(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts['later-b'],))
        incomplete = _facts(connection, complete=False)
        assert learning.choose_queue_action(incomplete, observed_at=FIXED_TIME).kind == 'pending'
        complete = _facts(connection)
        action = learning.choose_queue_action(complete, observed_at=FIXED_TIME)
        assert action.kind == 'early_retry' and action.identity.entry_id == result['requeue_entry_id']
        assert connection.execute('SELECT released_at FROM queue_retry_policies').fetchone()[0] is None


def test_expired_retry_requires_once_only_natural_placement_even_with_other_ready_work(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        observed = FIXED_TIME + timedelta(seconds=450)
        facts = _facts(connection, now=observed)
        assert facts.ready_head.entry_id == queued_attempts['later-b']
        action = learning.choose_queue_action(facts, observed_at=observed)
        assert action.kind == 'activate' and action.identity.entry_id == result['requeue_entry_id']


def test_already_issued_retry_stays_operable_without_repositioning_or_a_new_draw(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts['later-b'],))
        connection.execute('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=?', (result['requeue_entry_id'],))
        facts = _facts(connection)
        assert facts.ready_head.entry_id == result['requeue_entry_id'] and facts.ready_head.issued
        assert learning.choose_queue_action(facts, observed_at=FIXED_TIME).kind == 'issue'
        assert connection.execute('SELECT released_at FROM queue_retry_policies').fetchone()[0] is None


def test_valid_new_day_admission_retains_original_unelapsed_retry_constraint(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        next_day_entry = connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-19','active-a',0) RETURNING id").fetchone()[0]
        connection.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES('2026-10-19','later-b',1)")
        observed = FIXED_TIME + timedelta(minutes=3)
        facts = _facts(connection, day='2026-10-19', now=observed)
        assert facts.ready_head.card_id == 'later-b'
        connection.execute("UPDATE daily_queue SET status='buried' WHERE queue_date='2026-10-19' AND card_id='later-b'")
        facts = _facts(connection, day='2026-10-19', now=observed)
        assert facts.ready_head is None and facts.cooling_retry.identity.entry_id == next_day_entry
        assert facts.cooling_retry.ready_at == FIXED_TIME + timedelta(seconds=450)
        policy = connection.execute('SELECT * FROM queue_retry_policies').fetchone()
        assert policy['retry_queue_entry_id'] == result['requeue_entry_id'] and policy['released_at'] is None


@pytest.mark.parametrize('offset_microseconds,expected_ready', [(-1, False), (1, True)])
def test_chronological_history_supersedes_retry_only_at_the_actual_later_instant(queued_attempts, offset_microseconds, expected_ready):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts['later-b'],))
        # Equivalent non-UTC representation must not lose microsecond ordering.
        later = (FIXED_TIME + timedelta(microseconds=offset_microseconds)).astimezone(timezone(timedelta(hours=-4)))
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES('active-a','correct',?,0,1,'study')", (later.isoformat(),))
        facts = _facts(connection)
        assert bool(facts.ready_head) == expected_ready
        if facts.ready_head:
            assert facts.ready_head.entry_id == result['requeue_entry_id']


def test_invalidated_failure_and_future_scheduled_retry_are_never_early_candidates(queued_attempts):
    with database.connection() as connection:
        result = _failed_review(connection, queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts['later-b'],))
        connection.execute("UPDATE cards SET due_date='2026-10-20' WHERE id='active-a'")
        facts = _facts(connection)
        assert facts.ready_head is None and facts.cooling_retry is None and facts.due_retry is None
        connection.execute('UPDATE reviews SET invalidated_at=? WHERE id=?', (FIXED_TIME.isoformat(),result['review_id']))
        # Invalidating the policy cannot resurrect its cooling selection.
        facts = _facts(connection)
        assert facts.ready_head is None and facts.cooling_retry is None and facts.due_retry is None


@pytest.mark.parametrize('kind,cycle,state,priority', [
    ('explicit', 1, 'clean', None),
    ('review', 0, 'clean', 'Priority review · missed in a recent game'),
    ('review', 2, 'gameplay', 'Priority review · missed in a recent game'),
])
def test_existing_explicit_and_fresh_miss_authority_is_not_a_random_future_admission(queued_attempts, kind, cycle, state, priority):
    with database.connection() as connection:
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (queued_attempts['later-b'],))
        connection.execute("UPDATE cards SET due_date='2026-10-20' WHERE id='active-a'")
        connection.execute('UPDATE daily_queue SET admission_kind=?,cycle=?,attempt_state=?,gameplay_priority_reason=? WHERE id=?',
                           (kind,cycle,state,priority,queued_attempts['active-a']))
        assert _facts(connection).ready_head.entry_id == queued_attempts['active-a']


@pytest.mark.parametrize('released', [False,True])
def test_same_day_projection_rebuild_keeps_retry_deadline_and_requires_saved_placement(queued_attempts, released):
    with database.connection() as connection:
        result = _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        if released:
            connection.execute("UPDATE queue_retry_policies SET released_at=?,release_reason='no_ready_work'",(FIXED_TIME.isoformat(),))
        before = dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone())
        connection.execute('DELETE FROM daily_queue WHERE id=?',(result['requeue_entry_id'],))
        new_entry = connection.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,attempt_state) VALUES('2026-10-18','active-a',3,5,'review','guided') RETURNING id").fetchone()[0]
        facts = _facts(connection)
        assert facts.ready_head is None
        candidate = facts.due_retry if released else facts.cooling_retry
        assert candidate.identity.entry_id == new_entry
        assert dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone()) == before


def test_soft_fallback_releases_and_issues_one_retry_without_changing_original_due_time(queued_attempts):
    with database.connection() as connection:
        first = _failed_review(connection,queued_attempts,token='0'*32)
        second = _failed_review(connection,queued_attempts,card_id='later-b',token='f'*32)
        _complete_projection(connection)
        before = [dict(row) for row in connection.execute('SELECT * FROM queue_retry_policies ORDER BY review_id')]
        selected = _select(connection)
        assert selected.status == 'issued' and selected.identity.entry_id == first['requeue_entry_id']
        after = [dict(row) for row in connection.execute('SELECT * FROM queue_retry_policies ORDER BY review_id')]
        assert after[0]['released_at'] == after[0]['issued_at'] == FIXED_TIME.isoformat()
        assert after[0]['release_reason'] == 'no_ready_work'
        assert after[0]['ready_at'] == before[0]['ready_at']
        assert after[1] == before[1] and after[1]['retry_queue_entry_id'] == second['requeue_entry_id']
        positions = [tuple(row) for row in connection.execute('SELECT id,position FROM daily_queue ORDER BY id')]
        assert _select(connection,now=FIXED_TIME+timedelta(seconds=1)).identity == selected.identity
        assert [tuple(row) for row in connection.execute('SELECT id,position FROM daily_queue ORDER BY id')] == positions
        assert [dict(row) for row in connection.execute('SELECT * FROM queue_retry_policies ORDER BY review_id')] == after


def test_revalidated_ready_work_blocks_a_previously_proposed_early_retry(queued_attempts):
    from app.services.retry_queue import selection_store
    with database.connection() as connection:
        result = _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
        store = selection_store(connection)
        context = learning.QueueContext(FIXED_TIME,'2026-10-18')
        proposal = learning.choose_queue_action(store.queue_ready_facts(context),observed_at=FIXED_TIME)
        assert proposal.kind == 'early_retry'
        connection.execute("UPDATE daily_queue SET status='queued' WHERE id=?",(queued_attempts['later-b'],))
        assert store.apply_queue_action(proposal,context).status == 'pending'
        assert connection.execute('SELECT released_at FROM queue_retry_policies WHERE review_id=?',(result['review_id'],)).fetchone()[0] is None


@pytest.mark.parametrize('token,expected_status', [('0'*32,'issued'),('0'*31+'1','changed')])
def test_expired_retry_placement_uses_saved_draw_once_and_preserves_peer_order(queued_attempts,token,expected_status):
    with database.connection() as connection:
        result = _failed_review(connection,queued_attempts,token=token)
        _complete_projection(connection)
        observed = FIXED_TIME+timedelta(seconds=450)
        selected = _select(connection,now=observed)
        assert selected.status == expected_status
        policy = dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone())
        assert policy['released_at'] == observed.isoformat() and policy['release_reason'] == 'deadline'
        positions = [tuple(row) for row in connection.execute("SELECT id,position FROM daily_queue WHERE status='queued' ORDER BY position,id")]
        next_selection = _select(connection,now=observed+timedelta(seconds=1))
        expected_head = result['requeue_entry_id'] if expected_status=='issued' else queued_attempts['later-b']
        assert next_selection.status == 'issued' and next_selection.identity.entry_id == expected_head
        assert [tuple(row) for row in connection.execute("SELECT id,position FROM daily_queue WHERE status='queued' ORDER BY position,id")] == positions


def test_active_issued_head_prevents_retry_activation_from_moving_it(queued_attempts):
    with database.connection() as connection:
        _failed_review(connection,queued_attempts,token='0'*32)
        connection.execute('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=?',(queued_attempts['later-b'],))
        _complete_projection(connection)
        before = [tuple(row) for row in connection.execute('SELECT id,position FROM daily_queue ORDER BY id')]
        selected = _select(connection,now=FIXED_TIME+timedelta(minutes=10))
        assert selected.status == 'issued' and selected.identity.entry_id == queued_attempts['later-b']
        assert [tuple(row) for row in connection.execute('SELECT id,position FROM daily_queue ORDER BY id')] == before
        assert connection.execute('SELECT released_at FROM queue_retry_policies').fetchone()[0] is None


def test_queue_selection_requires_the_callers_sqlite_transaction(queued_attempts):
    with database.connection() as connection:
        with pytest.raises(RuntimeError,match='BEGIN IMMEDIATE'):
            _select(connection)


def test_release_failure_rolls_back_policy_placement_and_origin_issuance(queued_attempts):
    from app.services.retry_queue import selection_store
    with database.connection() as connection:
        _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
    with pytest.raises(RuntimeError,match='after release'):
        with database.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            store = selection_store(connection)
            def fail_issue(*_):
                assert connection.execute('SELECT released_at FROM queue_retry_policies').fetchone()[0] is not None
                raise RuntimeError('after release')
            store._issue = fail_issue
            learning.select_queue_head(store,learning.QueueContext(FIXED_TIME,'2026-10-18'))
    with database.read_connection() as connection:
        policy = connection.execute('SELECT * FROM queue_retry_policies').fetchone()
        assert policy['released_at'] is None and policy['issued_at'] is None
        assert connection.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?',(policy['retry_queue_entry_id'],)).fetchone()[0] == 0


def test_atomic_fallback_guard_sees_ready_arrival_after_the_last_read(queued_attempts):
    from app.services.retry_queue import selection_store
    with database.connection() as connection:
        _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
        class ReadyArrival:
            inserted = False
            def __getattr__(self,name): return getattr(connection,name)
            def execute(self,statement,arguments=()):
                if not self.inserted and statement.startswith('UPDATE queue_retry_policies SET retry_queue_entry_id='):
                    self.inserted = True
                    connection.execute("UPDATE daily_queue SET status='queued' WHERE id=?",(queued_attempts['later-b'],))
                return connection.execute(statement,arguments)
        raced = ReadyArrival()
        result = learning.select_queue_head(selection_store(raced),learning.QueueContext(FIXED_TIME,'2026-10-18'))
        assert raced.inserted and result.status == 'pending' and result.reason == 'ready_work_changed'
        assert connection.execute('SELECT released_at FROM queue_retry_policies').fetchone()[0] is None


def test_ready_work_beyond_twenty_cooling_rows_prevents_fallback(queued_attempts):
    from tempo_learning_runtime.retry_store import persist_failed_retry
    from app.services import queue_application
    with database.connection() as connection:
        _failed_review(connection,queued_attempts)
        for index in range(24):
            identifier = f'cooling-{index}'
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT ?,'issuance','prefix',start_fen,moves_json,'learning','2026-10-18','tactic' FROM cards WHERE id='active-a'",(identifier,))
            origin = connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES('2026-10-18',?,0,'complete') RETURNING id",(identifier,)).fetchone()[0]
            review_id = connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(?,'again',?,0,0,'study') RETURNING id",(identifier,FIXED_TIME.isoformat())).fetchone()[0]
            source = learning.QueueAttempt(origin,identifier,'2026-10-18',0,'review',1,False,'complete',None)
            persist_failed_retry(connection,learning.FailedRetryRequest(review_id,source,FIXED_TIME),
                draws=lambda: (450,'2'*32),
                create_entry=lambda identifier=identifier: queue_application.requeue(connection,'2026-10-18',identifier,None,'guided'))
        for position,row in enumerate(connection.execute('SELECT retry_queue_entry_id FROM queue_retry_policies ORDER BY review_id').fetchall()):
            connection.execute('UPDATE daily_queue SET position=? WHERE id=?',(position,row[0]))
        connection.execute('UPDATE daily_queue SET position=999 WHERE id=?',(queued_attempts['later-b'],))
        _complete_projection(connection)
        first_page = [row[0] for row in connection.execute(available_attempt_sql()+' ORDER BY q.position,q.id LIMIT 20',('2026-10-18',))]
        assert len(first_page)==20 and queued_attempts['later-b'] not in first_page
        result = _select(connection)
        assert result.status=='issued' and result.identity.entry_id==queued_attempts['later-b']
        assert connection.execute('SELECT COUNT(*) FROM queue_retry_policies WHERE released_at IS NOT NULL').fetchone()[0]==0


def test_repeated_failure_uses_actual_early_review_time_for_its_new_cycle(queued_attempts):
    with database.connection() as connection:
        first = _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
        assert _select(connection).status=='issued'
        reviewed_at = FIXED_TIME+timedelta(seconds=20)
        request = ReviewRequest(outcome='again',queue_entry_id=first['requeue_entry_id'],
            attempt_id='actual-early-cycle',recorded_at=reviewed_at.isoformat(),
            expected_review_id=first['review_id'],expected_revision=1)
        adapter = LearningReviewAdapter(connection,request,observed_at=reviewed_at,
            retry_draws=lambda: (600,'3'*32),
            policy_provider=lambda: SimpleNamespace(timezone='UTC',light_first_interval_days=7))
        command = command_from_request('active-a',request)
        second = learning.apply_review(adapter,command,adapter.context(command)).as_dict()
        reviews = [row[0] for row in connection.execute("SELECT reviewed_at FROM reviews WHERE card_id='active-a' ORDER BY id")]
        assert reviews==[FIXED_TIME.isoformat(),reviewed_at.isoformat()]
        policies = [dict(row) for row in connection.execute('SELECT * FROM queue_retry_policies ORDER BY review_id')]
        assert policies[0]['release_reason']=='no_ready_work' and policies[0]['ready_at']==(FIXED_TIME+timedelta(seconds=450)).isoformat()
        assert policies[1]['review_id']==second['review_id'] and policies[1]['ready_at']==(reviewed_at+timedelta(seconds=600)).isoformat()
        assert policies[1]['released_at'] is None and policies[1]['retry_queue_entry_id']!=first['requeue_entry_id']


def test_released_projection_rebind_preserves_first_release_and_placement_on_reload(queued_attempts):
    with database.connection() as connection:
        first = _failed_review(connection,queued_attempts)
        _complete_projection(connection)
        observed = FIXED_TIME+timedelta(seconds=450)
        assert _select(connection,now=observed).status=='changed'
        before = dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone())
        assert before['issued_at'] is None
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        connection.execute('DELETE FROM daily_queue WHERE id=?',(first['requeue_entry_id'],))
        replacement = connection.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,attempt_state) VALUES('2026-10-18','active-a',1,10,'review','guided') RETURNING id").fetchone()[0]
        selected = _select(connection,now=observed+timedelta(seconds=1))
        assert selected.status=='issued' and selected.identity.entry_id==replacement
        after = dict(connection.execute('SELECT * FROM queue_retry_policies').fetchone())
        assert after=={**before,'retry_queue_entry_id':replacement,'issued_at':(observed+timedelta(seconds=1)).isoformat()}


def test_missing_issued_retry_origin_never_authorizes_a_second_projection_attempt(queued_attempts):
    with database.connection() as connection:
        first = _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
        assert _select(connection).status=='issued'
        connection.execute('DELETE FROM daily_queue WHERE id=?',(first['requeue_entry_id'],))
        replacement = connection.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,attempt_state) VALUES('2026-10-18','active-a',1,10,'review','guided') RETURNING id").fetchone()[0]
        selected = _select(connection,now=FIXED_TIME+timedelta(seconds=1))
        assert selected.status=='pending' and selected.identity is None
        assert connection.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?',(replacement,)).fetchone()[0]==0
        assert connection.execute('SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=?',(first['requeue_entry_id'],)).fetchone()[0]==1


def test_two_sqlite_issuers_observe_one_released_retry_and_the_same_issued_head(queued_attempts):
    with database.connection() as connection:
        first = _failed_review(connection,queued_attempts,token='0'*32)
        _failed_review(connection,queued_attempts,card_id='later-b',token='f'*32)
        _complete_projection(connection)
    barrier = Barrier(2)
    def issue():
        barrier.wait(timeout=5)
        with database.connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            return _select(connection)
    with ThreadPoolExecutor(max_workers=2) as workers:
        selected = list(workers.map(lambda _: issue(),range(2)))
    assert all(result.status=='issued' and result.identity.entry_id==first['requeue_entry_id'] for result in selected)
    with database.read_connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM queue_retry_policies WHERE released_at IS NOT NULL').fetchone()[0]==1
        assert connection.execute("SELECT COUNT(*) FROM queue_attempt_origins WHERE issued_as_head=1 AND last_status='queued'").fetchone()[0]==1


def test_actual_retry_readiness_and_release_sql_parse_for_both_postgres_compositions(queued_attempts):
    from app.postgres_store import postgres_sql as legacy_sql
    from tempo_learning_runtime.postgres_store import postgres_sql as runtime_sql
    from app.services.retry_queue import selection_store
    statements = set()
    with database.connection() as connection:
        _failed_review(connection,queued_attempts)
        connection.execute("UPDATE daily_queue SET status='buried' WHERE id=?",(queued_attempts['later-b'],))
        _complete_projection(connection)
        class Capture:
            def __getattr__(self,name): return getattr(connection,name)
            def execute(self,statement,arguments=()):
                if 'queue_retry_policies' in statement:
                    statements.add(statement)
                return connection.execute(statement,arguments)
        assert learning.select_queue_head(selection_store(Capture()),learning.QueueContext(FIXED_TIME,'2026-10-18')).status=='issued'
    assert statements
    for statement in statements:
        assert legacy_sql(statement) and runtime_sql(statement)


def test_natural_retry_placement_preserves_peers_and_uses_one_bulk_write(queued_attempts):
    from app.services.retry_queue import selection_store
    with database.connection() as connection:
        failed = _failed_review(connection,queued_attempts,token='0'*32)
        for index in range(100):
            card_id = f'placement-peer-{index}'
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT ?,'issuance','prefix',start_fen,moves_json,'learning','2026-10-18','tactic' FROM cards WHERE id='active-a'",(card_id,))
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES('2026-10-18',?,?,'review')",(card_id,index+10))
        connection.execute('UPDATE daily_queue SET position=1000 WHERE id=?',(failed['requeue_entry_id'],))
        ordered_positions = available_attempt_sql().replace('SELECT q.id FROM','SELECT q.id,q.position FROM',1)+' ORDER BY q.position,q.id'
        before = [(row['id'],row['position']) for row in connection.execute(
            ordered_positions,('2026-10-18',)).fetchall()]
        peer_ids = [entry_id for entry_id,_ in before if entry_id!=failed['requeue_entry_id']]
        writes = []
        class CountWrites:
            def __getattr__(self,name): return getattr(connection,name)
            def execute(self,statement,arguments=()):
                if 'SET position=placement.position' in statement:
                    writes.append(statement)
                return connection.execute(statement,arguments)
        _complete_projection(connection)
        result = learning.select_queue_head(selection_store(CountWrites()),
            learning.QueueContext(FIXED_TIME+timedelta(seconds=450),'2026-10-18'))
        assert result.status=='issued' and result.identity.entry_id==failed['requeue_entry_id']
        after = [(row['id'],row['position']) for row in connection.execute(
            ordered_positions,('2026-10-18',)).fetchall()]
        assert [entry_id for entry_id,_ in after[1:]]==peer_ids
        assert sorted(position for _,position in after)==sorted(position for _,position in before)
        assert len(writes)==1
