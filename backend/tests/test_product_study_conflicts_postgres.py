"""Native Study conflict owner boundary; current Repertoire authority is a fake port.

Reuses the existing task-owned capped fixture/cleanup, never an external target.
Applies only the private owner DDL after the registered source schema053. This proves
native transactions/rows/locks, not global migration registration or C activation.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Barrier
import uuid

import psycopg
import pytest

from app.study.conflicts import ConflictCommand, ConflictError, Occurrence, StudyConflicts
from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore, current_study_conflicts
from test_product_study_conflicts import Authority, FENCE, NOW
from test_product_game_lapse_postgres import owned_postgres
from test_product_game_lapse import MemoryStore

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def conflict_database(owned_postgres):
    if not (ROOT/'database/tempo_legacy_database/ddl/migrations/054_product_repertoire_review.sql').exists():
        with owned_postgres() as database:
            database.execute_native((ROOT/'database/tempo_legacy_database/ddl/study_conflicts.sql').read_text())
    return owned_postgres


@dataclass
class Case:
    transaction: object
    scope: Occurrence
    other_repertoire: str
    affected: int
    unrelated_shared: int
    unrelated_card: int
    command: ConflictCommand


@pytest.fixture
def case(conflict_database):
    identity=uuid.uuid4().hex
    rep,other=identity+'-a',identity+'-b'
    card,unrelated=identity+'-card',identity+'-unrelated'
    route=identity+'-route'
    fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    state=MemoryStore().card
    with conflict_database() as database:
        for repertoire in (rep,other):
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Fixture','fixture',%s)",(repertoire,NOW.isoformat()))
        for target in (card,unrelated):
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,
                state,due_date,introduced_at,revision,interval_days,fsrs_card_json,stability,content_type)
                VALUES(%s,%s,'prefix',%s,'["e2e4"]','white','learning',%s,%s,1,20,%s,20,'opening')""",
                (target,rep,fen,NOW.date().isoformat(),(NOW-timedelta(days=5)).isoformat(),state.fsrs_card_json))
        for repertoire in (rep,other):
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(repertoire,card))
        database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
            VALUES(%s,%s,'Route','white',%s,'["e2e4"]',%s)""",(route,rep,fen,NOW.isoformat()))
        database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)",(rep,NOW.isoformat()))
        database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
            card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,
            last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,0,%s,%s,%s,'["e2e4"]','white','prefix',0,0,'[]')""",
            (rep,route,card,' '.join(fen.split()[:4]),fen))
        ids=[]
        for target,cycle,position,repertoire,attempt,failed in [
            (card,0,1,rep,'hinted',1),(card,1,0,other,'clean',0),(unrelated,0,2,rep,'clean',0)]:
            entry=database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,
                admission_repertoire_id,admission_kind,attempt_state,attempt_failed)
                VALUES(%s,%s,%s,%s,%s,'review',%s,%s) RETURNING id""",
                (NOW.date().isoformat(),target,cycle,position,repertoire,attempt,failed)).fetchone()[0]
            database.execute_native('UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s',(entry,))
            ids.append(entry)
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            source_kind,source_ref) VALUES(%s,'correct',%s,10,20,'study',%s)""",
            (card,(NOW-timedelta(days=1)).isoformat(),identity+'-actual-prior'))
    scope=Occurrence(rep,route,0,card,1)
    return Case(conflict_database,scope,other,*ids,ConflictCommand(identity,rep,FENCE,(scope,)))


def unchanged_learning(database, case):
    return tuple(tuple(row) for row in database.execute_native("""SELECT id,revision,state,due_date,
        introduced_at,interval_days,fsrs_card_json,stability FROM cards WHERE id=%s""",
        (case.scope.presentation_id,)).fetchall()),tuple(tuple(row) for row in database.execute_native(
            'SELECT * FROM reviews WHERE card_id=%s ORDER BY id',(case.scope.presentation_id,)).fetchall())


def queue(database, identity):
    return dict(database.execute_native('SELECT * FROM daily_queue WHERE id=%s',(identity,)).fetchone())


def test_native_scoped_suspend_guard_and_resolution_preserve_history_and_other_attempts(case):
    authority=Authority()
    with case.transaction() as database:
        baseline=unchanged_learning(database,case)
        own=queue(database,case.affected)
        shared=queue(database,case.unrelated_shared)
        unrelated=queue(database,case.unrelated_card)
        service=current_study_conflicts(database,authority=authority,clock=lambda:NOW)
        receipt=service.synchronize(case.command)
        assert receipt.suspended_queue_ids==(case.affected,) and receipt.status=='applied'
        assert queue(database,case.affected)=={**own,'status':'blocked'}
        assert queue(database,case.unrelated_shared)==shared and queue(database,case.unrelated_card)==unrelated
        assert unchanged_learning(database,case)==baseline
        def concurrent_source_edit():
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with case.transaction() as editor:
                    editor.execute_native("SET LOCAL lock_timeout='100ms'")
                    editor.execute_native('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=%s',
                                          (case.scope.repertoire_id,))
            return 'fenced'
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(concurrent_source_edit).result(timeout=5)=='fenced'
        store=PostgresConflictStore(database)
        assert store.block_state(case.scope.repertoire_id,(case.scope.route_id,))=='blocked'
        with pytest.raises(ConflictError,match='conflict_suspended'):
            store.guard_attempt(case.affected,case.scope.presentation_id,1)
        store.guard_attempt(case.unrelated_shared,case.scope.presentation_id,1)
        authority.eligible,authority.reason=True,None
        resolved=ConflictCommand(case.command.command_id+'-resolved',case.scope.repertoire_id,FENCE,(case.scope,))
        assert service.synchronize(resolved).restored_queue_ids==(case.affected,)
        assert store.block_state(case.scope.repertoire_id,(case.scope.route_id,)) is None
        assert queue(database,case.affected)==own and unchanged_learning(database,case)==baseline
        assert queue(database,case.unrelated_shared)==shared


def test_native_conflict_failure_before_receipt_rolls_back_every_mutation(case):
    with pytest.raises(RuntimeError,match='receipt failure'):
        with case.transaction() as database:
            store=PostgresConflictStore(database)
            def fail(*args): raise RuntimeError('receipt failure')
            store.save_receipt=fail
            StudyConflicts(store,Authority(),clock=lambda:NOW).synchronize(case.command)
    with case.transaction() as database:
        assert queue(database,case.affected)['status']=='queued'
        assert not PostgresConflictStore(database).blocked(case.scope.repertoire_id,case.scope.presentation_id,1)
        assert PostgresConflictStore(database).receipt(case.command.command_id) is None


def test_native_concurrent_same_command_has_one_receipt(case):
    barrier=Barrier(2)
    def apply():
        barrier.wait(timeout=5)
        with case.transaction() as database:
            return current_study_conflicts(database,authority=Authority(),clock=lambda:NOW).synchronize(case.command)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=[future.result(timeout=10) for future in (executor.submit(apply),executor.submit(apply))]
    assert results[0]==results[1]
    with case.transaction() as database:
        assert database.execute_native('SELECT COUNT(*) FROM study_conflict_receipts WHERE command_id=%s',
            (case.command.command_id,)).fetchone()[0]==1


def test_native_hold_survives_rebuild_and_guards_original_recovery(case):
    with case.transaction() as database:
        current_study_conflicts(database,authority=Authority(),clock=lambda:NOW).synchronize(case.command)
        database.execute_native('DELETE FROM daily_queue WHERE id=%s',(case.affected,))
        day=(NOW+timedelta(days=2)).date().isoformat()
        replacement=database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id)
            VALUES(%s,%s,0,%s) RETURNING id""",(day,case.scope.presentation_id,case.scope.repertoire_id)).fetchone()[0]
    with case.transaction() as database:
        store=PostgresConflictStore(database)
        cursor=''
        # One effect per call/transaction in production; this test's single held
        # target is isolated, and its cursor does not erase other fixture holds.
        rows=database.execute_native('SELECT scope_key FROM study_conflict_holds WHERE scope_key<%s ORDER BY scope_key DESC LIMIT 1',
                                     (case.scope.identity(),)).fetchall()
        cursor=rows[0][0] if rows else ''
        assert store.reconcile_queue(after=cursor)==case.scope.identity()
        assert queue(database,replacement)['status']=='blocked'
        with pytest.raises(ConflictError,match='conflict_suspended'):
            store.guard_attempt(case.affected,case.scope.presentation_id,1)
        store.guard_attempt(case.unrelated_shared,case.scope.presentation_id,1)


def test_native_ambiguous_scope_is_pending_and_preexisting_block_is_not_restored(case):
    with case.transaction() as database:
        database.execute_native('UPDATE daily_queue SET admission_repertoire_id=NULL WHERE id=%s',(case.affected,))
        service=current_study_conflicts(database,authority=Authority(),clock=lambda:NOW)
        receipt=service.synchronize(case.command)
        assert receipt.status=='pending' and receipt.unproven_queue_ids==(case.affected,)
        assert PostgresConflictStore(database).block_state(case.scope.repertoire_id,(case.scope.route_id,))=='pending'
        assert queue(database,case.affected)['status']=='queued'
        assert queue(database,case.unrelated_shared)['status']=='queued'
    # Another owner's existing block is never claimed or cleared by this owner.
    with case.transaction() as database:
        database.execute_native("UPDATE daily_queue SET status='blocked',admission_repertoire_id=%s WHERE id=%s",
                                (case.scope.repertoire_id,case.affected))
        authority=Authority()
        authority.eligible,authority.reason=True,None
        resolved=ConflictCommand(case.command.command_id+'-restore',case.scope.repertoire_id,FENCE,(case.scope,))
        assert not current_study_conflicts(database,authority=authority,clock=lambda:NOW).synchronize(resolved).restored_queue_ids
        assert queue(database,case.affected)['status']=='blocked'


def test_native_resolution_does_not_clear_another_active_scoped_hold(case):
    other_route=case.scope.route_id+'-other'
    with case.transaction() as database:
        database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
            SELECT %s,repertoire_id,name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE id=%s""",
            (other_route,case.scope.route_id))
        database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
            card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,
            last_decision_index,decision_fen_keys_json)
            SELECT repertoire_id,generation,%s,decision_index,card_id,decision_fen_key,starting_fen,moves_json,
                trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json
            FROM opening_graph_steps WHERE repertoire_id=%s AND line_id=%s""",
            (other_route,case.scope.repertoire_id,case.scope.route_id))
        second=Occurrence(case.scope.repertoire_id,other_route,0,case.scope.presentation_id,1)
        authority=Authority()
        service=current_study_conflicts(database,authority=authority,clock=lambda:NOW)
        both=ConflictCommand(case.command.command_id+'-both',case.scope.repertoire_id,FENCE,(case.scope,second))
        service.synchronize(both)
        authority.eligible,authority.reason=True,None
        first=ConflictCommand(case.command.command_id+'-one',case.scope.repertoire_id,FENCE,(case.scope,))
        assert not service.synchronize(first).restored_queue_ids
        assert queue(database,case.affected)['status']=='blocked'
        final=ConflictCommand(case.command.command_id+'-last',case.scope.repertoire_id,FENCE,(second,))
        assert service.synchronize(final).restored_queue_ids==(case.affected,)
        assert queue(database,case.affected)['status']=='queued'


def test_c1_durable_hold_survives_current_main_due_row_restoration(case):
    """#501's exact current-main failing observation, retained in the owning suite."""
    from tempo_legacy_database.adapters.queue_refresh.application.eligibility import _restore_eligible_due_queue_entries
    with case.transaction() as database:
        baseline=unchanged_learning(database,case)
        unrelated=queue(database,case.unrelated_shared)
        receipt=current_study_conflicts(database,authority=Authority(),clock=lambda:NOW).synchronize(case.command)
        assert receipt.suspended_queue_ids==(case.affected,)
        _restore_eligible_due_queue_entries(database,NOW.date().isoformat())
        assert PostgresConflictStore(database).blocked(case.scope.repertoire_id,case.scope.presentation_id,1)
        assert unchanged_learning(database,case)==baseline
        assert queue(database,case.unrelated_shared)==unrelated
        assert queue(database,case.affected)['status']=='blocked'


@pytest.mark.parametrize('removed',[False,True])
def test_c1_central_attempt_guard_preserves_conflict_through_failure_and_origin_recovery(case,removed):
    from types import SimpleNamespace
    from app.contracts.review_conflicts import ReviewConflict
    from app.services.study_attempt_guard import guard_current_attempt
    from app.queue_attempt_origins import recover_queue_entry,validate_failure_marker
    with case.transaction() as database:
        baseline=unchanged_learning(database,case)
        unrelated=queue(database,case.unrelated_shared)
        current_study_conflicts(database,authority=Authority(),clock=lambda:NOW).synchronize(case.command)
        request=SimpleNamespace(attempt_id='original-synthetic',queue_entry_id=case.affected,expected_revision=1)
        if removed:
            database.execute_native('DELETE FROM daily_queue WHERE id=%s',(case.affected,))
        with pytest.raises(ReviewConflict) as failure:
            (recover_queue_entry(database,case.scope.presentation_id,request) if removed else
             validate_failure_marker(database,case.affected,case.scope.presentation_id,1))
        assert failure.value.code=='study_occurrence_conflict_suspended' and failure.value.status_code==409
        guard_current_attempt(database,case.unrelated_shared,case.scope.presentation_id,1)
        assert queue(database,case.unrelated_shared)==unrelated
        assert unchanged_learning(database,case)==baseline


@pytest.mark.parametrize('scope',['other','unknown','invalid'])
def test_c1_due_restoration_respects_current_queue_scope(case,scope):
    from tempo_legacy_database.adapters.queue_refresh.application.eligibility import _restore_eligible_due_queue_entries
    with case.transaction() as database:
        current_study_conflicts(database,authority=Authority(),clock=lambda:NOW).synchronize(case.command)
        repertoire=case.other_repertoire if scope=='other' else None if scope=='unknown' else 'not-an-owner'
        database.execute_native('UPDATE daily_queue SET admission_repertoire_id=%s WHERE id=%s',(repertoire,case.affected))
        _restore_eligible_due_queue_entries(database,NOW.date().isoformat())
        assert queue(database,case.affected)['status']==('queued' if scope=='other' else 'blocked')
        assert queue(database,case.unrelated_shared)['status']=='queued'


@pytest.mark.parametrize('release',['resolved','new_revision'])
def test_c1_due_admission_defers_current_hold_and_allows_resolution_or_new_revision(case,release):
    from tempo_legacy_database.adapters.queue_refresh.application.seeding import due_cards
    with case.transaction() as database:
        baseline=unchanged_learning(database,case)
        unrelated=queue(database,case.unrelated_shared)
        authority=Authority()
        service=current_study_conflicts(database,authority=authority,clock=lambda:NOW)
        service.synchronize(case.command)
        day=(NOW+timedelta(days=2)).date().isoformat()
        assert case.scope.presentation_id not in {row[0] for row in due_cards(database,day)}
        assert unchanged_learning(database,case)==baseline and queue(database,case.unrelated_shared)==unrelated
        if release=='resolved':
            authority.eligible,authority.reason=True,None
            resolved=ConflictCommand(case.command.command_id+'-resolution',case.scope.repertoire_id,FENCE,(case.scope,))
            assert service.synchronize(resolved).restored_queue_ids==(case.affected,)
            assert unchanged_learning(database,case)==baseline
        else:
            database.execute_native('UPDATE cards SET revision=2 WHERE id=%s',(case.scope.presentation_id,))
        assert case.scope.presentation_id in {row[0] for row in due_cards(database,day)}
        assert queue(database,case.unrelated_shared)==unrelated
