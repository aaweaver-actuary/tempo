"""Actual optional handoff assembly, including borrowed head observation."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.product_composition import feedback_release,managed_runtime,next_card_priority,provider_priority
from test_product_delivery_qualification import native_handoff,owned_postgres


def test_release_capabilities_bind_real_implementations_and_detect_missing_authority(monkeypatch):
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    actual = feedback_release.feedback_capabilities()
    assert actual.policy_version == 'first_departure_v1' and actual.receipt_interface_version == '0.2.2'
    assert all(value is True for name,value in actual.model_dump().items() if name not in {
        'policy_version','receipt_interface_version'})
    monkeypatch.setattr(CurrentOwnerDecisions,'prepare_qualified',None)
    assert feedback_release.feedback_capabilities().current_authority is False


@pytest.mark.parametrize('runtime_id',[None,'a'*12+':'+'b'*32])
def test_optional_handoff_registration_opens_no_connection_or_provider_and_retains_study(monkeypatch,runtime_id):
    registered = {}
    def forbidden():
        pytest.fail('Optional registration opened a connection')
    monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:runtime_id)
    monkeypatch.setattr(next_card_priority,'register_current_next_card_priority',lambda **kwargs:registered.update(kwargs))
    feedback_release.register_feedback_next_card_priority(read=forbidden)
    assert registered['read'] is forbidden and callable(registered['capture'])
    assert callable(registered['local_handoff_prepare']) and callable(registered['source_pending'])
    # SQLite/unknown optional capability is a false hint, with no I/O.
    assert registered['source_pending'](object(),'2026-10-30') is False


def test_root_discards_preparation_when_fresh_boot_changes_before_writer(monkeypatch):
    registered = {}
    tokens = iter(('old','new'))
    monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:next(tokens))
    monkeypatch.setattr(next_card_priority,'register_current_next_card_priority',lambda **kwargs:registered.update(kwargs))
    monkeypatch.setattr(provider_priority,'prepare_determined_feedback',lambda **kwargs:SimpleNamespace(mode_runtime_id='old'))
    feedback_release.register_feedback_next_card_priority(read=lambda:pytest.fail('unused reader'))
    assert registered['local_handoff_prepare']() is None


def test_foreground_local_handoff_forwards_unreserved_snapshot_profile_without_a_writer(monkeypatch):
    from tempo_legacy_database import connections as owner
    registered, calls = {}, []
    @contextmanager
    def read_connection(*, repertoire_snapshot=False):
        calls.append(repertoire_snapshot)
        yield SimpleNamespace(raw='read-only-source')
    def prepare(**options):
        with options['connections'](repertoire_snapshot=True) as raw:
            assert raw == 'read-only-source'
        return None
    monkeypatch.setattr(owner, 'read_connection', read_connection)
    monkeypatch.setattr(managed_runtime, 'current_managed_runtime_identity', lambda: 'boot')
    monkeypatch.setattr(provider_priority, 'prepare_determined_feedback', prepare)
    monkeypatch.setattr(next_card_priority, 'register_current_next_card_priority', lambda **kwargs: registered.update(kwargs))
    feedback_release.register_feedback_next_card_priority()
    assert calls == []
    assert registered['local_handoff_prepare']() is None
    assert calls == [True]


def test_native_root_observes_source_and_applies_one_unit_only_after_receipt_fence(native_handoff,monkeypatch):
    """Full source/publications -> registered pure root -> same borrowed writer."""
    from app.services import queue_next_card
    from tempo_legacy_database.adapters.games import product_feedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    monkeypatch.setattr(queue_next_card,'_prepare',None)
    monkeypatch.setattr(queue_next_card,'_source_pending',None)
    feedback_release.register_feedback_next_card_priority(workspace=case.workspace,
        read=case.case.transaction,clock=case.clock)
    prepared = queue_next_card.prepare_next_card_command({})
    assert isinstance(prepared,feedback_release.PreparedCurrentGamesWork)
    assert prepared.work.original_delivery == case.original
    assert case.store.delivery(case.original.operation_id).state == 'pending'
    # Read hints use exactly the borrowed connection and transaction. Missing
    # optional schema/identity must not abort the caller or block ordinary Study.
    with case.case.transaction() as database:
        xid = database.execute_native('SELECT txid_current()').fetchone()[0]
        monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:pytest.fail('writer filesystem read'))
        monkeypatch.setattr(product_feedback.PostgresGamesStore,'_transaction',lambda *args:pytest.fail('writer opened a Games connection'))
        assert queue_next_card.pending_next_card_preparation(database,'2026-10-30')
        assert database.execute_native('SELECT txid_current()').fetchone()[0] == xid
        database.execute_native('ALTER TABLE product_feedback_deliveries RENAME TO source_temporarily_unavailable')
        assert not queue_next_card.pending_next_card_preparation(database,'2026-10-30')
        assert database.execute_native('SELECT txid_current()').fetchone()[0] == xid
        database.execute_native('ALTER TABLE source_temporarily_unavailable RENAME TO product_feedback_deliveries')
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        database.execute_native("SET LOCAL lock_timeout='100ms'")
        assert prepared.apply(database).progressed
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is not None
        assert database.execute_native("SELECT state FROM product_feedback_deliveries WHERE operation_id=%s",
            (case.original.operation_id,)).fetchone()[0] == 'acknowledged'


def test_native_pending_absence_and_cutoff_use_index_among_same_workspace_legacy_history(native_handoff,monkeypatch):
    """Actual planner, 5,000 due pending old rows, held/retry/control cutoffs."""
    from datetime import datetime,timedelta
    from psycopg.types.json import Jsonb
    from app.contracts.product_v02.learning import GameEffectControl
    from app.games.feedback.coordinator import delivery_for
    from tempo_legacy_database.adapters.games.product_feedback import (
        has_determined_delivery_in_transaction,handoff_priority_for,
    )
    case = native_handoff
    original_bytes = case.original.command.model_dump_json()
    legacy = case.original.model_copy(update={'qualification':None,'qualification_status':'ready'})
    control = delivery_for(case.source.source_key,GameEffectControl(key=case.original.command.key,
        source_revision=case.original.command.source.source_revision,action='hold',reason='reanalysis'))
    future_control = control.model_copy(update={'next_attempt_at':10**12})
    from pathlib import Path
    migration = (Path(__file__).resolve().parents[2]/'database/tempo_legacy_database/ddl/migrations/056_managed_game_feedback_mode.sql').read_text()
    backfill = migration[migration.index('UPDATE product_feedback_deliveries'):migration.index('CREATE INDEX product_feedback_determined_pending')]
    with case.case.transaction() as database:
        database.execute_native("""INSERT INTO product_feedback_deliveries
            (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
            SELECT %s||'-legacy-'||item,%s,%s,'encounter','pending',0,%s::jsonb
            FROM generate_series(1,5000) item""",
            (case.original.operation_id,case.source.source_key,case.original.command.key.logical_id(),
             Jsonb(legacy.model_dump(mode='json'))))
        database.execute_native("""INSERT INTO product_feedback_deliveries
            (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
            VALUES(%s,%s,%s,'control','pending',%s,%s::jsonb)""",
            (future_control.operation_id,future_control.source_key,future_control.command.key.logical_id(),
             future_control.next_attempt_at,future_control.model_dump_json()))
        database.execute_native('UPDATE product_feedback_deliveries SET handoff_priority=NULL WHERE operation_id=%s',
            (case.original.operation_id,))
        for statement in backfill.split(';'):
            if statement.strip():
                database.execute_native(statement)
        assert database.execute_native('SELECT handoff_priority FROM product_feedback_deliveries WHERE operation_id=%s',
            (case.original.operation_id,)).fetchone()[0] == handoff_priority_for(case.original)
        assert database.execute_native('SELECT handoff_priority FROM product_feedback_deliveries WHERE operation_id=%s',
            (future_control.operation_id,)).fetchone()[0] == ''
        assert database.execute_native("SELECT count(*) FROM product_feedback_deliveries WHERE operation_id LIKE %s AND handoff_priority IS NULL",
            (case.original.operation_id+'-legacy-%',)).fetchone()[0] == 5000
        database.execute_native('ANALYZE product_feedback_deliveries')
    held = case.original.model_copy(update={'qualification_status':'held','qualification_retryable':True,
        'qualification_reason':'first_departure_preparation_unavailable','next_attempt_at':10**12})
    case.store.save_delivery(held)
    plans = []
    def observed(*,cutoff=None):
        with case.case.transaction() as database:
            actual = database.execute_native
            statements = []
            def execute(statement,parameters=()):
                statements.append((statement,parameters))
                return actual(statement,parameters)
            monkeypatch.setattr(database,'execute_native',execute)
            result = has_determined_delivery_in_transaction(database,case.workspace,**(cutoff or {}))
            statement,parameters = statements[0]
            plan = actual('EXPLAIN (FORMAT JSON) '+statement,parameters).fetchone()[0]
            def indexes(value):
                if isinstance(value,dict):
                    return ([value['Index Name']] if 'Index Name' in value else []) + [
                        name for child in value.values() for name in indexes(child)]
                if isinstance(value,list):
                    return [name for child in value for name in indexes(child)]
                return []
            assert 'product_feedback_determined_pending' in indexes(plan),plan
            plans.append(plan)
            return result
    assert not observed()  # Due legacy rows cannot create authority; retry is not due.
    semantic = held.model_copy(update={'qualification_retryable':False,'next_attempt_at':0})
    case.store.save_delivery(semantic)
    assert not observed()
    due = semantic.model_copy(update={'qualification_status':'ready','qualification_retryable':True})
    case.store.save_delivery(due)
    determined = datetime.fromisoformat(due.determined_at.replace('Z','+00:00'))
    cutoff = dict(before_stream='forward',before_determined_at=(determined-timedelta(seconds=1)).isoformat(),
        before_effect_id='game-effect:'+'0'*64)
    assert not observed(cutoff=cutoff)
    assert observed(cutoff=dict(cutoff,before_determined_at=(determined+timedelta(seconds=1)).isoformat()))
    case.store.save_delivery(semantic)
    case.store.save_delivery(control)
    assert observed(cutoff=cutoff)  # Controls precede any ordinary/ready candidate.
    case.store.save_delivery(control.model_copy(update={'next_attempt_at':10**12}))
    assert not observed(cutoff=cutoff)
    assert case.store.delivery(case.original.operation_id).command.model_dump_json() == original_bytes
    assert len(plans) == 6


def test_native_new_source_before_prepared_successor_preserves_save_and_issues_target_first(native_handoff,monkeypatch):
    """Two-tab boundary: actual capture commits before ordinary authorization."""
    from datetime import datetime,timedelta,timezone
    from app import queue_commands
    from app.contracts.transport.study import ReviewRequest
    from app.product_composition.game_review import SystemClock
    from app.services import queue_next_card
    from app.services.learning_review_adapter import LearningReviewAdapter,command_from_request
    from tempo_learning_review import ReviewPolicy,submit_review
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    from tempo_legacy_database.adapters.study.issuance import attempt_issuance
    from product_encounter_fixtures import qualified_feedback_source
    from test_product_recombination_postgres import ordinary_current_review
    case,clock = native_handoff,SystemClock()
    day = datetime.now(timezone.utc).date().isoformat()
    active,ordinary = case.case.card_id+'-active',case.case.card_id+'-ordinary'
    monkeypatch.setattr(queue_next_card,'_prepare',None)
    monkeypatch.setattr(queue_next_card,'_source_pending',None)
    try:
        with case.case.transaction() as database:
            # Replace this fixture's earlier manual source with a genuine NEW
            # capture only after the protected board has actually been issued.
            database.execute_native('DELETE FROM product_feedback_deliveries WHERE source_key=%s',(case.source.source_key,))
            database.execute_native('DELETE FROM product_feedback_sources WHERE source_key=%s',(case.source.source_key,))
            database.execute_native('UPDATE imported_games SET played_at=%s WHERE id=%s',
                ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),case.original.command.key.game_id))
            database.execute_native('UPDATE daily_queue SET queue_date=%s,position=3 WHERE card_id=%s',
                (day,case.case.protected_id))
            for position,card_id in enumerate((active,ordinary)):
                database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,
                    state,due_date,content_type) SELECT %s,repertoire_id,kind,start_fen,moves_json,
                    'learning',%s,'tactic' FROM cards WHERE id=%s""",(card_id,day,case.case.card_id))
                database.execute_native('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)',
                    (day,card_id,position))
            ids = {row['card_id']:row['id'] for row in database.execute_native(
                'SELECT id,card_id FROM daily_queue WHERE card_id=ANY(%s)',([active,ordinary],)).fetchall()}
            assert queue_commands.issue_queue_head(database,dict(queue_date=day,entry_id=ids[active],
                card_id=active,expected_revision=1)) == {'issued':True}
            protected_origin = attempt_issuance(database,ids[active],active,1)
            assert protected_origin is not None
        # This speculative ordinary identity is retained by another tab before
        # the provider game is captured, but has no server-issued authority.
        successor = dict(queue_entry_id=ids[ordinary],card_id=ordinary,revision=1,cycle=0,local_date=day)
        captured = provider_priority._owner_capture(case.connections)(case.case.repertoire_id,
            case.original.command.key.game_id,(0,))
        template = case.case.command(captured)
        with qualified_feedback_source(case.case.transaction,capture=captured,command=template,
                clock=lambda:datetime.now(timezone.utc),capture_provider_source=True) as source:
            feedback_release.register_feedback_next_card_priority(workspace=case.workspace,
                read=case.case.transaction,clock=clock)
            assert datetime.fromisoformat(source.source.determined_at) > datetime.fromisoformat(protected_origin.issued_at)
            request = ReviewRequest(outcome='correct',queue_entry_id=ids[active],expected_revision=1,
                attempt_id=active+'-review',prepared_next=successor)
            with case.case.transaction() as database:
                # The existing issued board remains available across the new
                # pending hint, while a new ordinary attempt cannot get ahead.
                assert queue_commands.issue_queue_head(database,dict(queue_date=day,entry_id=ids[active],
                    card_id=active,expected_revision=1)) == {'issued':True}
                adapter = LearningReviewAdapter(database,request,observed_at=datetime.now(timezone.utc),
                    policy_provider=lambda:ReviewPolicy('UTC',7))
                command = command_from_request(active,request)
                saved = submit_review(adapter,command,lambda:adapter.context(command)).as_dict()
                assert saved['persisted'] and 'prepared_continuation' not in saved
                assert attempt_issuance(database,ids[ordinary],ordinary,1) is None
                assert database.execute_native('SELECT count(*) FROM reviews WHERE card_id=%s',(active,)).fetchone()[0] == 1
            for _ in range(2):  # One NEW handoff, then its already accepted Study application.
                prepared = queue_next_card.prepare_next_card_command({})
                with case.case.transaction() as database:
                    database.execute_native("SET LOCAL transaction_timeout='250ms'")
                    database.execute_native("SET LOCAL lock_timeout='100ms'")
                    result = queue_next_card.prepared_next_card_result(database,prepared)
                    assert result['progressed'],result
            with case.case.transaction() as database:
                effect = PostgresGameFeedbackStore(database).get(source.command.key)
                target = effect.progress.target.card_id
                entry = database.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'",
                    (target,)).fetchone()[0]
                assert queue_commands.issue_queue_head(database,dict(queue_date=day,entry_id=entry,
                    card_id=target,expected_revision=effect.progress.target.card_revision)) == {'issued':True}
                current = PostgresGameFeedbackStore(database).get(source.command.key)
                assert current.progress.first_issued_ordinal == 1
                assert current.progress.first_issued.issuance_sequence > protected_origin.issuance_sequence
                assert attempt_issuance(database,ids[ordinary],ordinary,1) is None
                review,_ = ordinary_current_review(database,target,monkeypatch)
            with case.case.transaction() as database:
                assert PostgresGameFeedbackStore(database).get(source.command.key).progress.settlement.review_id == review['review_id']
    finally:
        with case.case.transaction() as database:
            database.execute_native('DELETE FROM cards WHERE id=ANY(%s)',([active,ordinary],))
