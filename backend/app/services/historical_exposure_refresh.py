"""One durable, yielding card-scope refresh; no reviews or generic queue rebuild."""
from dataclasses import asdict
from datetime import datetime,timezone
from psycopg.errors import LockNotAvailable,SerializationFailure
from tempo_learning_review import historical_exposure as policy
from ..database import connection
from ..postgres_store import PostgresConnection
from ..storage.historical_exposure import pending_scope_for_card,retry_key
from ..storage.historical_exposure_facts import lock_card_scope
from ..storage.historical_exposure_practice import completed_practices
from .historical_exposure_adapter import locked_ports
from .activity_gate import activity_gate
from .redis_admission_gate import BackgroundAdmissionDeferred
from .durable_tasks import (enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,complete_task_slice_in_transaction)

TASK_KIND='historical_exposure_refresh'
REFRESH_SECONDS=60


def schedule_refresh(database,scope,*,delay_seconds=0):
    payload=asdict(scope)
    key=retry_key(scope)
    enqueue_compact_postgres_task_in_transaction(database,TASK_KIND,key,payload,
        priority=30,delay_seconds=delay_seconds)


def after_study_review(database,card_id):
    """Only checkpoint a wake; foreground never runs exposure facts/completion."""
    if not isinstance(database,PostgresConnection):return
    scope=pending_scope_for_card(database,card_id)
    if scope is not None:schedule_refresh(database,scope)


def execute_exposure_refresh_slice(task):
    scope=policy.CardScope(**task['payload'])
    activity_gate.wait_for_foreground()
    try:
        with connection(background=True) as database:
            lock_card_scope(database,scope)
            # Domain locks precede task-row fencing, matching enqueue callers.
            store,writer,reader=locked_ports(database,scope)
            if not lock_current_slice(database,task):return False
            now=datetime.now(timezone.utc)
            records=store.pending_for_card(scope)
            events=completed_practices(database,scope,records,now)
            policy.process_card(store,writer,reader,scope,events,now)
            if store.pending_for_card(scope):
                # This lease is operational only. Unserved training intent has
                # no age expiry, including when an operational retry fails.
                schedule_refresh(database,scope,delay_seconds=REFRESH_SECONDS)
                return True
            complete_task_slice_in_transaction(database,task)
            return False
    except (LockNotAvailable,SerializationFailure) as error:
        raise BackgroundAdmissionDeferred('Exposure source busy; retain refresh') from error
