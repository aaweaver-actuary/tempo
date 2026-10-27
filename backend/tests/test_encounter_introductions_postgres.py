"""Exact encounter admission on existing schema051, with no weighted D tables.

Use the existing capped task-owned PostgreSQL fixture. Schema051 is unchanged
published feedback-owner source, assembled for this qualification only.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import hashlib
import json
from pathlib import Path
import uuid
from threading import Barrier

import chess
import psycopg
import pytest

from app.contracts.product_v02.repertoire import DecisionPresentation
from app.services.queue_admission_adapter import current_owner_encounter_introductions
from tempo_evidence_math import opening_position_key
from tempo_game_evidence_contracts.legacy import decision_identity
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.queue_refresh.application.admission import product_source_facts
from test_product_introductions_postgres import owned_postgres

ROOT = Path(__file__).resolve().parents[2]
DAY = '2026-10-26'


@pytest.fixture(scope='module')
def canonical_transaction(owned_postgres):
    spec = importlib.util.spec_from_file_location('encounter_migrations',ROOT/'database/tooling/apply_postgres_migrations.py')
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)
    files = sorted(migrations.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    assert files[-1].name == '051_study_game_feedback.sql'
    with psycopg.connect(**owned_postgres) as raw:
        for path in files:
            registration = migrations.MODULE_MIGRATIONS.get(path.name)
            if registration:
                source = (ROOT/'database'/registration[2]).read_bytes()
                assert hashlib.sha256(source).hexdigest() == registration[3]
                raw.execute(source.decode(),prepare=False)
            raw.execute(path.read_text(),prepare=False)
        raw.execute('INSERT INTO settings(id) VALUES(1)')
        for table in ('state','periods','charges','rankings'):
            assert raw.execute('SELECT to_regclass(%s)',('study_product_introduction_'+table,)).fetchone()[0] is None

    @contextmanager
    def transaction():
        with psycopg.connect(**owned_postgres,row_factory=tempo_row_factory) as raw:
            raw.execute("SET LOCAL lock_timeout='3s'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)
    return transaction


@pytest.fixture
def encounter_case(canonical_transaction):
    identity = uuid.uuid4().hex
    repertoire, line, card = ('rep-'+identity,'line-'+identity,'card-'+identity)
    board = chess.Board()
    board.push_uci('e2e4'); board.push_uci('e7e5')
    with canonical_transaction() as database:
        database.execute('UPDATE settings SET study_only_mode=0,new_cards_per_day=10 WHERE id=1',())
        database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)',
            (repertoire,'Synthetic','fixture',DAY))
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,'white',?,?,?)",
            (line,repertoire,line,chess.STARTING_FEN,json.dumps(['e2e4','e7e5','g1f3']),DAY))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,?,'response',?,?,'locked',?,'opening','white')",
            (card,repertoire,board.fen(),json.dumps(['g1f3']),DAY))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(repertoire,card))
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,?,1,?,?,?,'[\"g1f3\"]','white')",
            (repertoire,line,card,opening_position_key(board.fen()),board.fen()))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(?,1,'ready',?)",(repertoire,DAY))
        for suffix in ('-due','-active'):
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color,introduced_at) VALUES(?,?,'response',?,'[\"g1f3\"]','learning',?,'opening','white','2026-10-25')",
                (card+suffix,repertoire,board.fen(),DAY))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,attempt_state) VALUES(?,?,0,'in_progress')",(DAY,card+'-active'))

    def target(database):
        header,_,cards,_ = product_source_facts(database,repertoire,(card,),(line,))
        return DecisionPresentation(decision_id=decision_identity(card,0,[]),repertoire_id=repertoire,
            position_key=opening_position_key(board.fen()),selected_response_uci='g1f3',presentation_id=card,
            presentation_revision=cards[0]['revision'],repertoire_revision=header['scope_source_revision']+1,
            introduction='unintroduced',introduced_at=None,available=True,
            introduction_matches_current_content=True)

    yield canonical_transaction,repertoire,card,target
    with canonical_transaction() as database:
        database.execute('DELETE FROM study_game_feedback WHERE card_id=?',(card,))
        database.execute('DELETE FROM daily_queue WHERE card_id=ANY(?)',([card,card+'-due',card+'-active'],))
        database.execute('DELETE FROM cards WHERE repertoire_id=?',(repertoire,))
        database.execute('DELETE FROM repertoires WHERE id=?',(repertoire,))
        database.execute('UPDATE settings SET study_only_mode=0,new_cards_per_day=10 WHERE id=1',())


def admit(database,target,*,allowance='ordinary',paused=False,**options):
    receiver = current_owner_encounter_introductions(database,allowance=allowance,paused=paused,**options)
    return receiver.admit_encounter(DAY,repertoire_id=target.repertoire_id,decision_id=target.decision_id,target=target)


def add_routes(database,repertoire,moves,count):
    for n in range(count):
        identity = uuid.uuid4().hex
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,'white',?,?,?)",
            (identity,repertoire,identity,chess.STARTING_FEN,json.dumps(moves),DAY))


@pytest.mark.parametrize('allowance',['ordinary','encounter_exception'])
def test_encounter_canonical_large_unrelated_source_and_caller_receipt_rollback(encounter_case,allowance):
    transaction,repertoire,card,mapping = encounter_case
    with transaction() as database:
        add_routes(database,repertoire,['d2d4','d7d5','c2c4'],80)
        target = mapping(database)
        active = tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card+'-active',)).fetchone())
        history = dict(database.execute('SELECT * FROM cards WHERE id=?',(card,)).fetchone())
    with pytest.raises(RuntimeError,match='caller rollback'):
        with transaction() as database:
            assert admit(database,target,allowance=allowance).status == 'introduced'
            database.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,card_id,decision_id,received_at,status,updated_at) VALUES(?,'{}','fixture',?,?,now(),'applied',now())",
                ('receipt-'+card,card,target.decision_id))
            raise RuntimeError('caller rollback')
    with transaction() as database:
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(card,)).fetchone()[0] is None
        assert database.execute('SELECT COUNT(*) FROM study_game_feedback WHERE card_id=?',(card,)).fetchone()[0] == 0
        assert admit(database,target,allowance=allowance).status == 'introduced'
        database.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,card_id,decision_id,received_at,status,updated_at) VALUES(?,'{}','fixture',?,?,now(),'applied',now())",
            ('receipt-'+card,card,target.decision_id))
    with transaction() as database:
        assert admit(database,target,allowance=allowance).status == 'already_introduced'
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == 1
        assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(card,)).fetchone()[0] == 0
        after = dict(database.execute('SELECT * FROM cards WHERE id=?',(card,)).fetchone())
        assert {key:value for key,value in after.items() if key not in {'state','introduced_at'}} == {
            key:value for key,value in history.items() if key not in {'state','introduced_at'}}
        assert tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card+'-active',)).fetchone()) == active
        assert [row[0] for row in database.execute('SELECT card_id FROM daily_queue WHERE card_id=ANY(?) ORDER BY position',([card,card+'-due'],)).fetchall()] == [card+'-due',card]


@pytest.mark.parametrize('change,reason',[
    ('study_only','study_only'),('zero_global','zero_limit'),('zero_repertoire','zero_limit'),
    ('spent_global','introduction_allowance'),('spent_repertoire','introduction_allowance'),
    ('stale','current_source_unproved'),('hold','current_target_unavailable'),
    ('conflicting_route','target_response_conflict'),('relevant_bound','target_alternatives_bound')])
@pytest.mark.parametrize('allowance',['ordinary','encounter_exception'])
def test_encounter_canonical_controls_and_complete_target_fences(encounter_case,change,reason,allowance):
    transaction,repertoire,card,mapping = encounter_case
    class Held:
        def blocked(self,rep,physical,revision): return True
    with transaction() as database:
        target = mapping(database)
        if change == 'study_only': database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1',())
        if change == 'zero_global': database.execute('UPDATE settings SET new_cards_per_day=0 WHERE id=1',())
        if change == 'zero_repertoire': database.execute('UPDATE repertoires SET new_cards_per_day=0 WHERE id=?',(repertoire,))
        if change in ('spent_global','spent_repertoire'):
            database.execute('UPDATE cards SET introduced_at=? WHERE id=?',(DAY,card+'-due'))
            if change == 'spent_global': database.execute('UPDATE settings SET new_cards_per_day=1 WHERE id=1',())
            else: database.execute('UPDATE repertoires SET new_cards_per_day=1 WHERE id=?',(repertoire,))
        if change in ('conflicting_route','relevant_bound','stale'):
            add_routes(database,repertoire,
                ['e2e4','e7e5','f1c4'] if change == 'conflicting_route' else ['e2e4','e7e5','g1f3'],
                64 if change == 'relevant_bound' else 1)
            if change != 'stale': target = mapping(database)
        controls = tuple(database.execute('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1',()).fetchone())
        result = admit(database,target,allowance=allowance,conflicts=Held() if change == 'hold' else None)
        exception = allowance == 'encounter_exception' and change in {
            'study_only','zero_global','zero_repertoire','spent_global','spent_repertoire'}
        assert (result.status,result.reason) == (('introduced',None) if exception else ('deferred',reason))
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(card,)).fetchone()[0] == (DAY if exception else None)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == int(exception)
        assert tuple(database.execute('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1',()).fetchone()) == controls


def test_encounter_requires_explicit_policy_and_exact_mapping(encounter_case):
    transaction,repertoire,card,mapping = encounter_case
    with transaction() as database:
        with pytest.raises(ValueError,match='explicit'):
            current_owner_encounter_introductions(database,allowance='limited_reserve',paused=False)
        with pytest.raises(ValueError,match='full-pause'):
            current_owner_encounter_introductions(database,allowance='encounter_exception',paused='false')
        receiver = current_owner_encounter_introductions(database,allowance='ordinary',paused=False)
        assert receiver.admit_encounter(DAY,repertoire_id=repertoire,decision_id=mapping(database).decision_id).reason == 'current_mapping_required'


def test_encounter_canonical_concurrent_targets_share_saved_global_cap(encounter_case):
    transaction,repertoire,card,mapping = encounter_case
    other = card+'-other'
    with transaction() as database:
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) SELECT ?,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color FROM cards WHERE id=?",(other,card))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(repertoire,other))
        database.execute('INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) SELECT repertoire_id,generation,line_id,decision_index+1,?,decision_fen_key,starting_fen,moves_json,trained_color FROM opening_graph_steps WHERE card_id=?',(other,card))
        database.execute('UPDATE settings SET new_cards_per_day=1 WHERE id=1',())
        first = mapping(database)
        second = first.model_copy(update=dict(presentation_id=other,decision_id=decision_identity(other,0,[])))
    gate = Barrier(2)
    def worker(target):
        with transaction() as database:
            gate.wait(timeout=3)
            result = admit(database,target)
            return result.status,result.reason
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(worker,(first,second)))
    assert sorted(results,key=lambda item:item[0]) == [('deferred','introduction_allowance'),('introduced',None)]
    with transaction() as database:
        assert database.execute('SELECT COUNT(*) FROM cards WHERE repertoire_id=? AND introduced_at=?',(repertoire,DAY)).fetchone()[0] == 1


def test_encounter_canonical_rechecks_explicit_generated_membership(encounter_case):
    transaction,repertoire,card,mapping = encounter_case
    with transaction() as database:
        database.execute('UPDATE cards SET canonical_route_source=0 WHERE id=?',(card,))
        database.execute('UPDATE repertoire_cards SET canonical_route_source=0 WHERE card_id=?',(card,))
        target = mapping(database)
        database.execute('DELETE FROM repertoire_cards WHERE repertoire_id=? AND card_id=?',(repertoire,card))
        # Generated membership removal need not advance the authored revision.
        # A primary owner fallback must not manufacture the old exact graph link.
        assert mapping(database).repertoire_revision == target.repertoire_revision
        assert admit(database,target).reason == 'current_target_unavailable'


def test_encounter_exception_respects_full_pause_without_rewriting_ordinary_controls(encounter_case):
    transaction,repertoire,card,mapping = encounter_case
    with transaction() as database:
        database.execute('UPDATE settings SET study_only_mode=1,new_cards_per_day=0 WHERE id=1',())
        database.execute('UPDATE repertoires SET new_cards_per_day=0 WHERE id=?',(repertoire,))
        target = mapping(database)
        assert admit(database,target,allowance='encounter_exception',paused=True).reason == 'paused'
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(card,)).fetchone()[0] is None
        assert admit(database,target,allowance='ordinary').reason == 'study_only'
        assert admit(database,target,allowance='encounter_exception').status == 'introduced'
        assert tuple(database.execute('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1',()).fetchone()) == (1,0)
        assert database.execute('SELECT new_cards_per_day FROM repertoires WHERE id=?',(repertoire,)).fetchone()[0] == 0


def test_encounter_exception_races_ordinary_admission_on_same_physical_card_once(encounter_case):
    transaction,repertoire,card,mapping = encounter_case
    with transaction() as database:
        target = mapping(database)
    gate = Barrier(2)
    def worker(allowance):
        with transaction() as database:
            gate.wait(timeout=3)
            return admit(database,target,allowance=allowance).status
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(worker,('ordinary','encounter_exception')))
    assert results.count('introduced') == 1
    assert all(value in {'introduced','already_introduced','deferred'} for value in results)
    with transaction() as database:
        actual = tuple(database.execute('SELECT * FROM cards WHERE id=?',(card,)).fetchone())
        assert admit(database,target,allowance='encounter_exception').status == 'already_introduced'
        assert admit(database,target,allowance='ordinary').status == 'already_introduced'
        assert tuple(database.execute('SELECT * FROM cards WHERE id=?',(card,)).fetchone()) == actual
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == 1
        assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(card,)).fetchone()[0] == 0


def test_encounter_capability_never_loads_weighted_allocation_or_store(encounter_case,monkeypatch):
    import builtins
    transaction,repertoire,card,mapping = encounter_case
    original = builtins.__import__
    def guarded(name,*args,**kwargs):
        if name in {'app.study.introductions.application','app.study.introductions.ports',
                    'app.study.introductions.policy','app.contracts.product_v02.analysis'} or name.endswith('.product_introductions'):
            raise AssertionError('weighted runtime loaded by canonical encounter: '+name)
        return original(name,*args,**kwargs)
    with transaction() as database:
        target = mapping(database)
        with monkeypatch.context() as imports:
            imports.setattr(builtins,'__import__',guarded)
            assert admit(database,target,allowance='encounter_exception').status == 'introduced'
