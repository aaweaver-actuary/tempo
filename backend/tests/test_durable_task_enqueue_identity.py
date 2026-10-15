"""#144: events/results must use the identity selected by the UPSERT."""
import pytest
from app.services import durable_tasks


@pytest.mark.parametrize('existing', [None, {'id':'existing-task','generation':7,'created_at':'2026-10-15T12:00:00Z'}], ids=['concurrent-first-creator','existing-generation-replacement'])
def test_enqueue_events_and_result_use_postgres_returned_identity(monkeypatch, existing):
    authoritative={'id':'database-selected-task','generation':23,'created_at':'2026-10-15T12:00:00Z','replaced_pending_generation':1}
    events=[]
    class Cursor:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row
    class Database:
        def execute(self,statement,parameters):
            if statement==durable_tasks._TASK_BY_KIND_SQL:return Cursor(existing)
            if statement.startswith(durable_tasks._TASK_UPSERT_SQL):
                assert statement.endswith(' RETURNING *')
                return Cursor(authoritative)
            assert statement==durable_tasks._TASK_BY_ID_SQL
            assert parameters==('database-selected-task',)
            return Cursor(authoritative)
    monkeypatch.setattr(durable_tasks,'_record_event',lambda db,task_id,generation,event,phase,**kwargs:events.append((task_id,generation,event)))
    result=durable_tasks.enqueue_task_in_transaction(Database(),'identity-regression','coalesced',{'value':1})
    assert result==authoritative
    assert events==[('database-selected-task',23,'enqueued'),('database-selected-task',23,'generation_replaced')]
