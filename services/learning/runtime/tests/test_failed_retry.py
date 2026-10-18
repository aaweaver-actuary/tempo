"""Native composition contracts for soft failed retries; owning runner only."""
from datetime import datetime, timedelta, timezone

import pytest
import tempo_learning_review as learning
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.postgres_store import PostgresConnection
from tempo_learning_runtime.retry_queue import selection_store
from tempo_learning_runtime.remediation_adapter import request_remediation
from tempo_learning_runtime.real_game_feedback import MISS_REASON
from test_runtime import activate, head, review_payload, submit
from test_remediation import accepted, evidence
from test_evidence_revisions import drain


def failed_card(case):
    definition = activate(case)['publication']['definitions'][0]
    card = head(case)
    response = submit(case,card,review_payload(card,outcome='again'))
    assert response.status_code==200,response.text
    drain(case)
    return definition,card,response.json()


@pytest.mark.parametrize('task_state',['queued','leased','retrying','failed'])
def test_runtime_pending_remediation_admission_blocks_all_cooling_fallback(case,task_state):
    definition,_,result = failed_card(case)
    day = case.query('SELECT queue_date FROM learning.daily_queue WHERE id=%s',(result['requeue_entry_id'],))[0][0]
    assert case.query('SELECT refresh_pending FROM learning.queue_projections WHERE queue_date=%s',(day,))[0][0]==0
    accepted(case,evidence(definition))
    case.query("UPDATE learning.background_tasks SET state=%s WHERE kind='remediation' AND deduplication_key='current'",(task_state,))
    with case.database.scope(), connection() as database:
        context = learning.QueueContext(datetime.now(timezone.utc),day)
        store = selection_store(database)
        assert not store.queue_ready_facts(context).complete
        assert learning.select_queue_head(store,context).status=='pending'
    assert case.query('SELECT released_at FROM learning.queue_retry_policies WHERE review_id=%s',(result['review_id'],))[0][0] is None


def test_runtime_atomic_release_rechecks_remediation_arrival_after_last_read(case,monkeypatch):
    _,_,result = failed_card(case)
    day = case.query('SELECT queue_date FROM learning.daily_queue WHERE id=%s',(result['requeue_entry_id'],))[0][0]
    execute = PostgresConnection.execute
    observed = []
    with case.database.scope(), connection() as selecting:
        def arrival(self,statement,parameters=()):
            if self is selecting and not observed and statement.startswith('UPDATE queue_retry_policies SET retry_queue_entry_id='):
                with connection() as other:
                    request_remediation(other,day)
                observed.append(True)
            return execute(self,statement,parameters)
        monkeypatch.setattr(PostgresConnection,'execute',arrival)
        selected = learning.select_queue_head(selection_store(selecting),
            learning.QueueContext(datetime.now(timezone.utc),day))
        assert observed and selected.status=='pending'
    assert case.query('SELECT released_at FROM learning.queue_retry_policies WHERE review_id=%s',(result['review_id'],))[0][0] is None


def test_runtime_recovered_again_retains_current_canonical_miss_priority(case):
    definition = activate(case)['publication']['definitions'][0]
    accepted(case,evidence(definition))
    drain(case)
    card = head(case)
    completed_at=datetime.now(timezone.utc).isoformat()
    # The retained issued origin remains valid although its projection vanished.
    case.query('DELETE FROM learning.daily_queue WHERE id=%s',(card['queue_entry_id'],))
    response = submit(case,card,review_payload(card,outcome='again',recorded_at=completed_at))
    assert response.status_code==200,response.text
    result = response.json()
    row = case.query('SELECT attempt_state,gameplay_priority_reason FROM learning.daily_queue WHERE id=%s',
        (result['requeue_entry_id'],))[0]
    assert tuple(row)==('guided',MISS_REASON)
    assert case.query('SELECT reviewed_at FROM learning.reviews WHERE id=%s',(result['review_id'],))[0][0]==completed_at
    policy=case.query('SELECT ready_at,delay_seconds FROM learning.queue_retry_policies WHERE review_id=%s',(result['review_id'],))[0]
    assert datetime.fromisoformat(policy[0])==datetime.fromisoformat(completed_at)+timedelta(seconds=policy[1])
    assert case.query('SELECT settled FROM learning.remediation_review_receipts WHERE review_id=%s',
        (str(result['review_id']),))[0][0] is False


def test_runtime_required_selector_binding_failure_rolls_back_frontier_and_origin(case,monkeypatch):
    from tempo_learning_runtime import remediation_adapter
    definition = activate(case)['publication']['definitions'][0]
    accepted(case,evidence(definition))
    drain(case)
    day = case.query("SELECT queue_date FROM learning.daily_queue WHERE status='queued' ORDER BY position,id LIMIT 1")[0][0]
    bind = remediation_adapter.bind_issued_attempt
    def fail_after_bind(*arguments):
        bind(*arguments)
        raise RuntimeError('required runtime frontier binding')
    monkeypatch.setattr(remediation_adapter,'bind_issued_attempt',fail_after_bind)
    with pytest.raises(RuntimeError,match='required runtime frontier binding'):
        with case.database.scope(), connection() as database:
            learning.select_queue_head(selection_store(database),
                learning.QueueContext(datetime.now(timezone.utc),day))
    assert case.query('SELECT COUNT(*) FROM learning.remediation_attempts')[0][0]==0
    assert case.query('SELECT COUNT(*) FROM learning.queue_attempt_origins WHERE issued_as_head=1')[0][0]==0
