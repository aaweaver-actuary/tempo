"""Named pure/compatibility regressions; native proofs use the disposable script."""
import json
import sqlite3
import pytest
from app.services import frozen_opening_order as order
from tempo_legacy_database.ddl.frozen_opening_schema import SCHEMA
from app.study.openings.frozen_math import content_fingerprint
from app.study.openings.capacity_frontier import plan_prioritized_opening_admissions


@pytest.fixture
def database():
    database=sqlite3.connect(':memory:');database.row_factory=sqlite3.Row
    database.executescript('''
    CREATE TABLE cards(id TEXT PRIMARY KEY,revision INTEGER,start_fen TEXT,moves_json TEXT,trained_color TEXT,repertoire_id TEXT,state TEXT,introduced_at TEXT);
    CREATE TABLE repertoire_cards(card_id TEXT,repertoire_id TEXT);
    CREATE TABLE repertoire_integrity_card_blocks(card_id TEXT,repertoire_id TEXT);
    CREATE TABLE reviews(card_id TEXT);
    CREATE TABLE opening_evidence_attempts(card_id TEXT);
    CREATE TABLE teaching_states(card_id TEXT);
    CREATE TABLE queue_attempt_origins(card_id TEXT,issued_as_head INTEGER,attempt_failed INTEGER,review_result_json TEXT,last_status TEXT,legacy INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE daily_queue(id INTEGER,card_id TEXT,attempt_failed INTEGER,review_result_json TEXT,status TEXT,attempt_state TEXT);
    ''')
    for statement in SCHEMA:database.execute(statement)
    for identifier in ('a','b','c'):
        database.execute("INSERT INTO cards VALUES(?,1,'fen','[]','white','repertoire','new',NULL)",(identifier,))
    yield database
    database.close()


def plan(database, run_id='run'):
    ranks=[]
    for rank,identifier in enumerate(('b','a'),1):
        card=dict(database.execute('SELECT * FROM cards WHERE id=?',(identifier,)).fetchone())
        ranks.append({'id':identifier,'rank':rank,'fingerprint':content_fingerprint(card,['repertoire']),'memberships':['repertoire']})
    return {'run_id':run_id,'checksum':run_id,'ranks':ranks}


def activate(database):
    publication=plan(database);order.stage_run(database,publication,'today');order.stage_ranks(database,publication,publication['ranks']);order.activate_run(database,publication)
    return publication


def candidate(identifier, repertoire='repertoire', **extra):
    return {'id':identifier,'repertoire_id':repertoire,'gameplay_priority_reason':None,'priority_date':None,'priority_score':100 if identifier=='a' else 0,'completed_line_ids_json':'[]','frontier_decisions_json':'[]','moves_json':'[]',**extra}


def test_frozen_annotation_preserves_generator_and_inactive_input_behavior(database):
    # No active order requires no sortable identities, rank reads or fingerprints.
    inputs = [{'card_id': None}, {'id': 'card'}]
    assert order.annotate(database, (row for row in inputs)) == [
        {**row, 'frozen_token': [None, 0]} for row in inputs]
    activate(database)
    assert [row['id'] for row in order.annotate(database, (candidate(card) for card in ('b', 'a')))] == ['b', 'a']


def test_frozen_ranks_override_generated_scores_breadth_and_ordinary_gameplay(database):
    activate(database)
    rows=order.annotate(database,[candidate('a',gameplay_priority_reason='ordinary boost'),candidate('b'),candidate('c')])
    selected=plan_prioritized_opening_admissions(rows,{},'today',3,miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['b','a','c']


def test_scored_rank_precedes_deferred_tail_rank_in_actual_admission_selector(database):
    publication=plan(database)
    rows_by_id={row['id']:row for row in publication['ranks']}
    scored={**rows_by_id['a'],'rank':100,'scoring_status':'scored','final_disposition':'scored','marginal_score':0.0}
    deferred={**rows_by_id['b'],'rank':35379,'scoring_status':'unscored_deferred','final_disposition':'deferred','marginal_score':None}
    publication['ranks']=[scored,deferred]
    order.stage_run(database,publication,'today')
    order.stage_ranks(database,publication,publication['ranks'])
    order.activate_run(database,publication)
    candidates=order.annotate(database,[candidate('b'),candidate('a')])
    selected=plan_prioritized_opening_admissions(candidates,{},'today',2,miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['a','b']


def test_frozen_global_order_preserves_shared_single_admission_quotas_and_mandatory_miss(database):
    activate(database)
    rows=order.annotate(database,[candidate('a'),candidate('b'),candidate('b','shared'),candidate('c',gameplay_priority_reason=order.MISS_REASON)])
    selected=plan_prioritized_opening_admissions(rows,{},'today',{'repertoire':1,'shared':1},miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['c','b']
    assert selected[1][0]=='shared'


def test_changed_content_is_held_and_new_cards_follow_frozen_backlog(database):
    activate(database);database.execute("UPDATE cards SET revision=2 WHERE id='b'")
    selected=plan_prioritized_opening_admissions(order.annotate(database,[candidate('a'),candidate('b'),candidate('c')]),{},'today',3,miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['a','c']
    assert order.annotate(database,[candidate('b')])[0]['frozen_valid'] is False


def test_idempotent_publication_stale_checkpoint_and_rollback_preserve_later_study(database):
    publication=activate(database)
    assert order.activate_run(database,publication) is False
    database.execute("INSERT INTO reviews VALUES('a')")
    assert order.rollback_run(database,'run') is None
    assert database.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==1
    assert not order.proposal_valid(database,{'card_id':'a','reason':None,'frozen_token':['run',1]})
    with pytest.raises(ValueError,match='Rollback conflict'):order.rollback_run(database,'run')


def test_stale_plan_guard_rejects_activation(database):
    publication=plan(database);order.stage_run(database,publication,'today');order.stage_ranks(database,publication,publication['ranks'])
    database.execute("UPDATE frozen_opening_runs SET invalidated=1 WHERE run_id='run'")
    with pytest.raises(ValueError,match='Stale plan'):order.activate_run(database,publication)
    assert order.activation(database)==(None,0)


def test_unstarted_slots_reorder_without_moving_issued_attempt_or_unrelated_review(database):
    activate(database)
    rows=[{'id':1,'card_id':'a','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None},
          {'id':2,'card_id':'c','content_type':'opening','admission_kind':'review','gameplay_priority_reason':None},
          {'id':3,'card_id':'b','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None}]
    assert [row['card_id'] for row in order.order_queue_slots(database,rows)]==['b','c','a']
    database.execute("INSERT INTO queue_attempt_origins(card_id,issued_as_head,attempt_failed,review_result_json,last_status) VALUES('a',1,0,NULL,'queued')")
    assert [row['card_id'] for row in order.order_queue_slots(database,rows)]==['a','c','b']


def test_unranked_legacy_origin_is_protected_but_new_unranked_fallback_remains(database):
    activate(database)
    database.execute("INSERT INTO queue_attempt_origins(card_id,legacy) VALUES('c',1)")
    annotated=order.annotate(database,[candidate('c')])[0]
    assert annotated['frozen_rank'] is None
    assert annotated['frozen_legacy_protected'] is True and annotated['frozen_valid'] is False
    assert plan_prioritized_opening_admissions([annotated],{},'today',1,miss_reason=order.MISS_REASON)==[]
    database.execute("DELETE FROM queue_attempt_origins WHERE card_id='c'")
    fallback=order.annotate(database,[candidate('c')])[0]
    assert fallback['frozen_rank'] is None and fallback['frozen_valid'] is True
    assert [row['id'] for _,row in plan_prioritized_opening_admissions(
        [fallback],{},'today',1,miss_reason=order.MISS_REASON)]==['c']


def test_unranked_legacy_origin_stays_protected_with_ordinary_gameplay_boost(database):
    activate(database)
    database.execute("INSERT INTO queue_attempt_origins(card_id,legacy) VALUES('c',1)")
    annotated=order.annotate(database,[candidate('c',gameplay_priority_reason='ordinary boost')])
    assert plan_prioritized_opening_admissions(annotated,{},'today',1,miss_reason=order.MISS_REASON)==[]


def test_proposal_rechecks_legacy_evidence_and_rejects_forged_miss_reason(database,monkeypatch):
    activate(database)
    database.execute("INSERT INTO queue_attempt_origins(card_id,legacy) VALUES('c',1)")
    old_proposal={**candidate('c'),'card_id':'c','reason':None,
                  'frozen_token':['run',1]}
    assert not order.proposal_valid(database,old_proposal)
    from app.services import real_game_feedback_adapter as real_game_feedback
    monkeypatch.setattr(real_game_feedback,'has_outstanding_real_game_miss',lambda database,card_id:False)
    forged={**old_proposal,'reason':order.MISS_REASON}
    assert not order.proposal_valid(database,forged)


def test_legacy_unstarted_queue_slot_is_not_permuted(database):
    activate(database)
    database.execute("INSERT INTO queue_attempt_origins(card_id,legacy) VALUES('c',1)")
    rows=[{'id':1,'card_id':'c','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None},
          {'id':2,'card_id':'a','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None}]
    assert [row['card_id'] for row in order.order_queue_slots(database,rows)]==['c','a']


def test_partial_teaching_and_attempted_cards_are_exposure_not_admission(database):
    assert not order.card_exposed(database,'a')
    database.execute("INSERT INTO teaching_states VALUES('a')")
    database.execute("INSERT INTO opening_evidence_attempts VALUES('b')")
    assert order.card_exposed(database,'a') and order.card_exposed(database,'b')


def test_frozen_capacity_uses_existing_parent_gate_without_disabling_expansion():
    from app.services.opening_capacity_admission import plan_frozen_with_capacity
    from app.study.openings.capacity_frontier import CapacityRoute
    rows=[candidate('parent',frozen_rank=2,frozen_valid=True,frozen_token=['run',1]),candidate('ordinary',frozen_rank=3,frozen_valid=True,frozen_token=['run',1])]
    child=candidate('child',frozen_rank=1,frozen_valid=True,frozen_token=['run',1])
    routes=(('child',CapacityRoute('repertoire',1,'line',1,'parent',False)),)
    selected=plan_frozen_with_capacity('today',rows,{}, {'repertoire':3},routes,[child],miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['parent','child','ordinary']
    assert 'capacity_route' in selected[1][1]


def test_frozen_shared_rank_uses_an_unblocked_membership(database):
    activate(database)
    database.execute("INSERT INTO repertoire_integrity_card_blocks VALUES('b','repertoire')")
    rows=order.annotate(database,[candidate('b'),candidate('b','shared'),candidate('a')])
    selected=plan_prioritized_opening_admissions(rows,{},'today',{'repertoire':1,'shared':1},miss_reason=order.MISS_REASON)
    assert selected[0][0]=='shared' and selected[0][1]['id']=='b'


def test_override_replacement_restarts_eligibility_before_reusing_old_checkpoint(monkeypatch):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as worker, study_mode
    @contextmanager
    def connection(**kwargs):yield object()
    captured={};unlocked=[]
    monkeypatch.setattr(worker.postgres_store,'configured',lambda:True)
    monkeypatch.setattr(worker,'background_read_connection',connection)
    monkeypatch.setattr(worker,'connection',connection)
    monkeypatch.setattr(order,'activation',lambda database,**kwargs:('new-run',2))
    monkeypatch.setattr(study_mode,'study_only_enabled',lambda:True)
    monkeypatch.setattr(worker,'lock_current_slice',lambda *args:True)
    monkeypatch.setattr(worker.activity_gate,'wait_for_foreground',lambda:None)
    monkeypatch.setattr(worker.queue_application,'_unlock_eligible_opening_cards',lambda *args,**kwargs:unlocked.append(True))
    monkeypatch.setattr(worker,'advance_task_slice_in_transaction',lambda database,task,**kwargs:captured.update(kwargs) or True)
    assert worker.execute_postgres_queue_refresh_slice({'payload':{'queue_date':'2026-10-14','_queue_phase':'study_opening_item','frozen_token':['old-run',1]}})
    assert unlocked==[True] and captured['next_phase']=='block_opening'
    assert captured['next_payload']['frozen_token']==['new-run',2]


def test_post_snapshot_unstarted_openings_follow_captured_slots_without_moving_reviews(database):
    activate(database)
    rows=[{'id':1,'card_id':'c','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None},
          {'id':2,'card_id':'a','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None},
          {'id':3,'card_id':'b','content_type':'opening','admission_kind':'new','gameplay_priority_reason':None}]
    assert [row['card_id'] for row in order.order_queue_slots(database,rows)]==['b','a','c']


def test_rollback_restores_only_owned_unchanged_queue_permutations(database):
    for field in ('source_ref','source_fen'):database.execute(f'ALTER TABLE cards ADD COLUMN {field} TEXT')
    for field,type_name in (('queue_date','TEXT'),('position','INTEGER')):database.execute(f'ALTER TABLE daily_queue ADD COLUMN {field} {type_name}')
    activate(database)
    database.executemany("INSERT INTO daily_queue VALUES(?,?,0,NULL,'queued','clean','today',?)",[(1,'a',0),(2,'b',1)])
    order.record_queue_changes(database,'today',[{'id':1,'position':1},{'id':2,'position':0}])
    database.execute('UPDATE daily_queue SET position=1-position')
    assert order.restore_queue_changes(database,'run')=={'restored_queue_entries':[1,2],'queue_conflicts':[]}
    assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue ORDER BY position')]==['a','b']
    database.execute('UPDATE daily_queue SET position=1-position')
    database.execute("INSERT INTO queue_attempt_origins(card_id,legacy) VALUES('a',1)")
    assert order.restore_queue_changes(database,'run')=={'restored_queue_entries':[],'queue_conflicts':[1,2]}
    database.execute("DELETE FROM queue_attempt_origins WHERE card_id='a'")
    database.execute("INSERT INTO teaching_states VALUES('a')")
    assert order.restore_queue_changes(database,'run')=={'restored_queue_entries':[],'queue_conflicts':[1,2]}
    assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue ORDER BY position')]==['b','a']


def test_serving_holds_changed_memberships_and_presentation_kind(database):
    for field in ('source_ref','source_fen','kind','content_type'):database.execute(f'ALTER TABLE cards ADD COLUMN {field} TEXT')
    database.execute('ALTER TABLE daily_queue ADD COLUMN gameplay_priority_reason TEXT')
    database.execute("UPDATE cards SET kind='prefix',content_type='opening'")
    activate(database)
    database.execute("INSERT INTO daily_queue VALUES(1,'a',0,NULL,'queued','clean',NULL)")
    statement='SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE 1=1'+order.stale_queue_predicate()
    assert len(database.execute(statement).fetchall())==1
    database.execute("INSERT INTO repertoire_cards VALUES('a','added')")
    assert not database.execute(statement).fetchall()
    database.execute("DELETE FROM repertoire_cards WHERE repertoire_id='added'")
    database.execute("UPDATE cards SET kind='checkpoint' WHERE id='a'")
    assert not database.execute(statement).fetchall()


def test_changed_captured_content_does_not_trust_a_forged_mandatory_reason(database,monkeypatch):
    activate(database);database.execute("UPDATE cards SET revision=2 WHERE id='b'")
    rows=order.annotate(database,[candidate('a'),candidate('b',gameplay_priority_reason=order.MISS_REASON)])
    selected=plan_prioritized_opening_admissions(rows,{},'today',0,miss_reason=order.MISS_REASON)
    assert [row['id'] for _,row in selected]==['b']
    from app.services import real_game_feedback_adapter as real_game_feedback
    monkeypatch.setattr(real_game_feedback,'has_outstanding_real_game_miss',lambda database,card_id:False)
    assert not order.proposal_valid(database,{'card_id':'b','reason':order.MISS_REASON,'frozen_token':['run',1]})
