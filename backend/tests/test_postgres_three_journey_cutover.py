"""Focused guard contract; PostgreSQL cutover proof is separately required."""
import copy
import json

import pytest

from database.tooling import verify_postgres_cli_state as guard


class FakeDatabase:
    def execute(self, query, params=None):
        assert query == 'SELECT to_regclass(%s)'
        class Result:
            def fetchone(self):
                return (None,)
        return Result()


def test_cut62_guard_retains_full_fixed_ledger_and_rejects_tamper(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    layout = {name: [['id'], [['id', 'text', True]]] for name in source}
    state = {name: {'count': 1, 'digest': name} for name in source}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    monkeypatch.setattr(guard, '_cut62_catalog', lambda database, secret: {'count': 1, 'digest': 'catalog'})
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'sequences'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    monkeypatch.setattr(guard, '_cut62_layout', lambda database, names: {name: layout[name] for name in names})
    monkeypatch.setattr(guard, '_cut62_table', lambda database, name, key, secret: state[name].copy())
    witness = guard.capture_cut62(FakeDatabase(), b'x' * 32)
    assert len(guard.CUT62_PROTECTED_TABLES) == 184
    assert guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)['to'] == 62
    name = next(iter(guard.CUT62_PROTECTED_TABLES))
    state[name]['digest'] = 'tampered'
    with pytest.raises(RuntimeError, match='retained rows changed'):
        guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)
    changed = copy.deepcopy(witness)
    del changed['rows'][name]
    with pytest.raises(RuntimeError, match='coverage'):
        guard.verify_cut62(FakeDatabase(), changed, b'x' * 32)
    changed['version'] = 99
    with pytest.raises(RuntimeError, match='unsupported'):
        guard.verify_cut62(FakeDatabase(), changed, b'x' * 32)


def test_cut62_witness_is_private_exclusive_and_authenticated(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, 'capture_cut62', lambda database, secret: {'version': 1})
    path = tmp_path / 'original.json'
    guard.save_cut62_witness(path, None)
    assert path.stat().st_mode & 0o777 == 0o600
    assert guard.load_cut62_witness(path)[0] == {'version': 1}
    with pytest.raises(FileExistsError):
        guard.save_cut62_witness(path, None)
    payload = json.loads(path.read_text())
    payload['witness']['version'] = 2
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match='authentication'):
        guard.load_cut62_witness(path)


def test_cut62_layout_rejects_unexpected_table(monkeypatch):
    monkeypatch.setattr(guard, 'table_layout', lambda db: {'extra': (('id',), (('id', 'text', True),))})
    with pytest.raises(RuntimeError, match='table coverage'):
        guard._cut62_layout(None, {'expected'})


def test_cut62_rejects_reintroduced_retired_relation(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    witness = {'version': 2, 'from': 61, 'to': 62,
               'layout': dict.fromkeys(source), 'rows': dict.fromkeys(source - {guard.CUT62_LEDGER})}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    class Database:
        def execute(self, query, params):
            class Result:
                def fetchone(self):
                    return ('still_exists',)
            return Result()
    with pytest.raises(RuntimeError, match='retired relation remains'):
        guard.verify_cut62(Database(), witness, b'x' * 32)


def test_cut62_sequence_drift_is_rejected(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    layout = {name: [['id'], [['id', 'text', True]]] for name in source}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    monkeypatch.setattr(guard, '_cut62_layout', lambda database, names: {name: layout[name] for name in names})
    monkeypatch.setattr(guard, '_cut62_table', lambda database, name, key, secret: {'count': 1, 'digest': name})
    monkeypatch.setattr(guard, '_cut62_catalog', lambda database, secret: {'count': 1, 'digest': 'catalog'})
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'before'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    witness = guard.capture_cut62(FakeDatabase(), b'x' * 32)
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'after'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    with pytest.raises(RuntimeError, match='sequences changed'):
        guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)


def test_cut62_native_schema61_to62(tmp_path):
    """Explicitly allocated empty local DB only; never an ambient/serving DB."""
    import os
    from urllib.parse import urlparse
    import psycopg
    from psycopg.types.json import Jsonb
    from database.tooling.apply_postgres_migrations import MIGRATIONS, apply_migrations, migration_source

    dsn = os.environ.get('TEMPO_W07_NATIVE_REGRESSION_DSN')
    if not dsn:
        pytest.skip('W07 native disposable database not allocated')
    parsed = urlparse(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.path != '/w07_regression':
        raise ValueError('refusing non-W07 disposable database')
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT to_regclass('public.tempo_schema_migrations')").fetchone()[0] is None
        for path in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql')):
            if int(path.name[:3]) <= 61:
                db.execute(migration_source(path), prepare=False)
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('synthetic','synthetic','synthetic','2026-01-01')")
        for card, kind in [('opening','opening'), ('defense','defense'), ('endgame','endgame'), ('tactic','tactic')]:
            db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'synthetic','prefix','synthetic','[]','2026-01-01',%s)", (card,kind))
        db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('fixture-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')")
        db.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('finding','fixture-game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('candidate','finding','fixture-game',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','defense')")
        db.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('attempt','candidate','defense',1,'e2e4','{}','2026-01-01')")
        db.execute("INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES ('template','endgame','synthetic','K','K','white','{}','2026-01-01')")
        db.execute("INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES ('end-attempt','template','synthetic','draw','2026-01-01')")
        db.execute("INSERT INTO next_opponent_snapshots(version,account,method_version,profile_json,published_at) VALUES ('version','synthetic','1','{}','2026-01-01')")
        db.execute("INSERT INTO next_opponent_accounts(account,profile_version) VALUES ('synthetic','version')")
        db.execute("INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES ('fixture-game','2026-01-01')")
        db.execute("INSERT INTO game_analysis_position_reports(id,game_id,analysis_version,scan_pass,position_index,request_json,state,updated_at) VALUES ('report','fixture-game',1,'shallow',0,'{}','queued','2026-01-01')")
        db.execute("INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES ('report','fixture-game','synthetic','2026-01-01')")
        db.execute("INSERT INTO game_analysis_publications(game_id,analysis_version,analysis_evidence_version,prepared_json,result_json,updated_at) VALUES ('fixture-game',1,1,'{}','{}','2026-01-01')")
        db.execute("INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval) VALUES (700001,'opening','again','2026-01-01',1,0)")
        db.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (700001,'2026-01-01','opening',1,'issued')")
        db.execute("INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('capture','tactic','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}')")
        db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES ('phone','review.submit','synthetic','pending','{\"card_id\":\"opening\"}')")
        pgn = '[Event "Synthetic"]\n\n1. e4 e5 *'
        db.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES ('source',1,%s)", (Jsonb({'raw_pgn': pgn}),))
        db.execute("INSERT INTO product_feedback_captures(source_key,source_revision,source_digest,comparison_revision,payload) VALUES ('source',1,'synthetic','1',%s)", (Jsonb({'raw_pgn': pgn}),))
        db.execute("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES ('delivery','source','effect','again','pending',0,'{\"card_id\":\"opening\"}')")
        db.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id,applied_at) VALUES ('effect','{}','1','decision',now(),'applied',now(),'opening',now())")

    path = tmp_path / 'cut62-original.json'
    with psycopg.connect(dsn) as db:
        guard.save_cut62_witness(path, db)
    witness, secret = guard.load_cut62_witness(path)
    assert witness['version'] == 2
    assert all(witness['rows'][name]['count'] == 1 for name in guard.CUT62_RETIRED_TABLES)
    assert witness['sequences']['retired']['count'] == 1
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        guard.verify_cut62(db, witness, secret)
        assert db.execute("SELECT to_regclass('public.game_analysis_position_errors_id_seq')").fetchone()[0] is None
        old = copy.deepcopy(witness)
        old['version'] = 1
        with pytest.raises(RuntimeError, match='unsupported'):
            guard.verify_cut62(db, old, secret)
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        guard.verify_cut62(db, witness, secret)
        db.execute("UPDATE repertoires SET name='tampered' WHERE id='synthetic'")
        with pytest.raises(RuntimeError, match='retained rows changed'):
            guard.verify_cut62(db, witness, secret)
        db.rollback()
        guard.verify_cut62(db, witness, secret)
        sequence_before = db.execute('SELECT last_value,is_called FROM reviews_id_seq').fetchone()
        try:
            db.execute("SELECT setval('reviews_id_seq',5,true)")
            with pytest.raises(RuntimeError, match='sequences changed'):
                guard.verify_cut62(db, witness, secret)
        finally:
            db.execute("SELECT setval('reviews_id_seq',%s,%s)", sequence_before)
        guard.verify_cut62(db, witness, secret)
        db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('synthetic','lichess','synthetic','2026-01-01T00:00:00Z','rapid',1,'white','draw','synthetic','[]')")
        for name in guard.CUT62_RETIRED_TABLES:
            assert db.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0] is None
        db.rollback()


def test_cut62_sequence_inventory_separates_removed_identity():
    class Result:
        def __init__(self, value):
            self.value = value
        def fetchall(self):
            return self.value
        def fetchone(self):
            return self.value
    class SequenceDB:
        def __init__(self, names):
            self.names = names
        def execute(self, statement):
            if isinstance(statement, str):
                return Result([(name, 'owner', '', 1, 1, 100, 1, 1, False)
                               for name in sorted(self.names)])
            return Result((1, False))
    source = guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES), b'x' * 32, True)
    target = guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b'x' * 32, False)
    assert source['protected'] == target['protected']
    assert source['retired']['count'] == 1
    assert target['retired']['count'] == 0
    with pytest.raises(RuntimeError, match='sequence coverage'):
        guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b'x' * 32, True)
    with pytest.raises(RuntimeError, match='sequence coverage'):
        guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES), b'x' * 32, False)
