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
    assert len(guard.CUT62_PROTECTED_TABLES) == 181
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
    witness = {'version': guard.CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62,
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
        db.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status,review_result_json) VALUES (700002,'2026-01-01','defense',1,'complete','{\"scheduled\":true}')")
        db.execute("INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref,invalidated_at) VALUES (700002,'defense','again','2026-01-01',1,0,'study','defense:attempt','2026-01-02')")
        db.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('attempt','candidate','defense',700002,1,'e2e4','{\"status\":\"incorrect\",\"diagnostic\":\"original\"}','2026-01-01')")
        db.execute("INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES ('template','endgame','synthetic','K','K','white','{}','2026-01-01')")
        db.execute("INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES ('end-attempt','template','synthetic','draw','2026-01-01')")
        db.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES ('synthetic-pack',1)")
        db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,card_id) VALUES ('synthetic-puzzle','synthetic-pack','tactic')")
        db.execute("INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at) VALUES ('2025-12-31',1,'{\"historical\":true}','complete','2026-01-01')")
        db.execute("INSERT INTO daily_chess_insights(id,local_day,kind,evidence_json,created_at,updated_at) VALUES ('insight','2026-01-01','synthetic','{}','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO daily_statistics_jobs(local_day,updated_at) VALUES ('2026-01-01','2026-01-01')")
        db.execute("INSERT INTO game_insight_recommendations(motif,miss_count,total_loss_cp,supporting_games_json,recommended_pack_id,updated_at) VALUES ('fork',1,100,'[\"fixture-game\"]','synthetic-pack','2026-01-01')")
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

    with psycopg.connect(dsn) as db:
        original_attempt = db.execute("SELECT row_to_json(a)::text FROM defense_attempts a WHERE attempt_id='attempt'").fetchone()[0]
    path = tmp_path / 'cut62-original.json'
    with psycopg.connect(dsn) as db:
        captured_id = guard.save_cut62_witness(path, db)
    witness, secret = guard.load_cut62_witness(path)
    assert witness['rows']['defense_attempts']['count'] == 1
    assert witness['version'] == 5
    assert all(witness['rows'][name]['count'] == 1 for name in guard.CUT62_RETIRED_TABLES)
    assert witness['sequences']['retired']['count'] == 1
    with psycopg.connect(dsn) as db:
        assert guard.check_captured_cut62(db, witness, secret)['witness_id'] == captured_id
        db.execute("UPDATE endgame_attempts SET target='win' WHERE id='end-attempt'")
        alternate_path = tmp_path / 'cut62-alternate.json'
        alternate_id = guard.save_cut62_witness(alternate_path, db)
        alternate, alternate_secret = guard.load_cut62_witness(alternate_path)
        assert guard.check_captured_cut62(db, alternate, alternate_secret)['witness_id'] == alternate_id
        assert alternate_id != captured_id
        db.rollback()
        db.execute("UPDATE repertoires SET name='tampered-before-cut' WHERE id='synthetic'")
        with pytest.raises(RuntimeError, match='source changed'):
            guard.check_captured_cut62(db, witness, secret)
        db.rollback()
        assert guard.check_captured_cut62(db, witness, secret)['unchanged']
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        assert guard.verify_cut62(db, witness, secret)['witness_id'] == captured_id
        assert guard.verify_cut62(db, alternate, alternate_secret)['witness_id'] == alternate_id
        assert alternate_id != captured_id  # Caller must reject a valid swapped witness.
        assert db.execute("SELECT row_to_json(a)::text FROM defense_attempts a WHERE attempt_id='attempt'").fetchone()[0] == original_attempt
        db.execute("UPDATE defense_attempts SET move_uci='d2d4' WHERE attempt_id='attempt'")
        with pytest.raises(RuntimeError, match='retained rows changed in defense_attempts'):
            guard.verify_cut62(db, witness, secret)
        db.rollback()
        guard.verify_cut62(db, witness, secret)
        assert db.execute("SELECT to_regclass('public.game_analysis_position_errors_id_seq')").fetchone()[0] is None
        old = copy.deepcopy(witness)
        old['version'] = 4
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


def test_schema61_defense_exclusive_rehearsal_preserves_shared_history(tmp_path):
    """Prove exact synthetic Defense deletion and rollback amid shared history."""
    import os
    from urllib.parse import urlparse
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get('TEMPO_W07_DEFENSE_CENSUS_DSN')
    if not dsn:
        pytest.skip('W07 defense-census disposable database not allocated')
    parsed = urlparse(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.path != '/w07_census':
        raise ValueError('refusing non-W07 disposable database')
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        assert db.execute('SELECT max(version) AS v FROM tempo_schema_migrations').fetchone()['v'] == 61
        try:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('census-rep','synthetic','synthetic','2026-01-01')")
            for card, kind in [('census-opening','opening'),('census-tactic','tactic'),('census-defense','defense'),('census-exclusive','defense'),('census-issued','defense'),('census-shared','defense'),('census-unknown','future-type')]:
                db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'census-rep','prefix','synthetic','[]','2026-01-01',%s)", (card,kind))
            # Exclusive Defense card with shared source/request identities; no review or recognition.
            db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('exclusive-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')")
            db.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('exclusive-finding','exclusive-game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')")
            db.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('exclusive-candidate','exclusive-finding','exclusive-game',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','census-exclusive')")
            db.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('mixed-candidate','exclusive-finding','exclusive-game',1,'mixed',2,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','census-shared')")
            db.execute("INSERT INTO threat_analysis_requests(id,request_json,created_at,updated_at) VALUES ('shared-request','{}','2026-01-01','2026-01-01')")
            db.execute("INSERT INTO threat_candidate_requests(candidate_id,request_id,role) VALUES ('exclusive-candidate','shared-request','best')")
            db.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status,admission_source) VALUES (900004,'2026-01-01','census-exclusive',4,'queued','defense:exclusive-candidate')")
            db.execute("UPDATE queue_attempt_origins SET legacy=1 WHERE queue_entry_id=900004")
            db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES ('accepted-line','census-rep','accepted','white','synthetic','[\"e2e4\"]','2026-01-01')")
            db.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status,admission_source) VALUES (900001,'2026-01-01','census-opening',1,'issued',NULL),(900002,'2026-01-01','census-defense',2,'queued','defense:candidate'),(900003,'2026-01-01','census-issued',3,'issued','defense:issued')")
            db.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id IN (900001,900003)")
            db.execute("INSERT INTO queue_attempt_abandonments(queue_entry_id,card_id,revision,operation_id,request_sha256,origin_sha256,actor,reason,abandoned_at) VALUES (900003,'census-issued',1,'abandoned',repeat('a',64),repeat('b',64),'synthetic','synthetic',now())")
            db.execute("INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_ref) VALUES (900002,'census-defense','again','2026-01-01',1,0,'defense:attempt')")
            db.execute("INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES (900002,'{}')")
            db.execute("INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json) VALUES ('census-receipt','census-defense',900002,'complete',0,900002,'complete','{}')")
            db.execute("INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('defense-theme','census-tactic','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}'),('mixed-capture','census-shared','game','2026-01-01','{}','{}')")
            db.execute("INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,created_at,reader_version) VALUES ('census-run','census-hash','{}','2026-01-01','1')")
            db.execute("INSERT INTO frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json) VALUES ('census-run','census-opening',1,'synthetic','[]','{}')")
            db.execute("UPDATE frozen_opening_activation SET run_id='census-run',epoch=1 WHERE id=1")
            db.execute("INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES ('census-rep','epoch','synthetic',now())")
            db.execute("INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at,donor_card_id,donor_card_revision,donor_fingerprint,donor_selection_policy) VALUES ('census-rep','standard','position','e2e4','1','epoch','synthetic',4503599627370496,'{}',now(),'census-opening',1,'synthetic','synthetic')")
            db.execute("INSERT INTO study_position_issued_plans(attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,owner_epoch,source_digest,issue_study_day,plan_digest,plan_json,issued_at) VALUES ('position-plan',900001,'census-opening',1,'census-rep','standard','epoch','synthetic','2026-01-01','synthetic','{}',now())")
            db.execute("INSERT INTO study_position_first_responses(event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,payload_digest,payload_json,observed_at,accepted_at) VALUES ('first-response','position-plan',0,'census-rep','standard','position','epoch','synthetic','{}',now(),now())")
            db.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id,applied_at) VALUES ('game-effect','{}','1','decision',now(),'applied',now(),'census-opening',now())")
            db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES ('mixed-envelope','unknown','synthetic','complete','{\"cards\":[\"census-defense\",\"census-opening\"]}')")
            cards = db.execute("SELECT id,content_type FROM cards WHERE id LIKE 'census-%' ORDER BY id").fetchall()
            assert {row['id'] for row in cards if row['content_type']=='defense'} == {'census-defense','census-exclusive','census-issued','census-shared'}
            origins = db.execute("SELECT queue_entry_id,card_id,revision,content_type,issued_as_head FROM queue_attempt_origins WHERE card_id LIKE 'census-%' ORDER BY queue_entry_id").fetchall()
            assert [(row['queue_entry_id'],row['card_id'],row['revision'],row['issued_as_head']) for row in origins] == [(900001,'census-opening',1,1),(900002,'census-defense',1,0),(900003,'census-issued',1,1),(900004,'census-exclusive',1,0)]
            assert db.execute("SELECT count(*) AS n FROM tactic_captures WHERE id IN ('defense-theme','mixed-capture')").fetchone()['n'] == 2
            assert db.execute("SELECT count(*) AS n FROM study_position_first_responses WHERE event_id='first-response'").fetchone()['n'] == 1
            assert db.execute("SELECT count(*) AS n FROM study_game_feedback WHERE effect_key='game-effect' AND status='applied'").fetchone()['n'] == 1
            assert db.execute("SELECT count(*) AS n FROM repertoire_lines WHERE id='accepted-line'").fetchone()['n'] == 1
            assert db.execute("SELECT count(*) AS n FROM review_attempt_receipts WHERE attempt_id='census-receipt' AND queue_entry_id=900002 AND review_id=900002").fetchone()['n'] == 1
            assert db.execute("SELECT count(*) AS n FROM operation_receipts WHERE operation_id='mixed-envelope'").fetchone()['n'] == 1
            # Independent Tactics ownership excludes an otherwise typed Defense candidate.
            assert db.execute("SELECT count(*) AS n FROM threat_training_candidates t JOIN tactic_captures capture ON capture.card_id=t.card_id WHERE t.id='mixed-candidate'").fetchone()['n'] == 1
            # The exact candidate link and its original legacy typed origin agree.
            assert db.execute("SELECT count(*) AS n FROM cards c JOIN threat_training_candidates t ON t.card_id=c.id JOIN daily_queue q ON q.card_id=c.id JOIN queue_attempt_origins o ON (o.queue_entry_id,o.card_id,o.revision)=(q.id,c.id,c.revision) WHERE c.id='census-exclusive' AND c.content_type='defense' AND o.content_type='defense' AND o.legacy=1 AND q.admission_source='defense:' || t.id").fetchone()['n'] == 1
            assert db.execute("SELECT count(*) AS n FROM threat_candidate_requests WHERE candidate_id='exclusive-candidate' AND request_id='shared-request'").fetchone()['n'] == 1
            # Rehearse only this exact synthetic identity; shared source rows remain.
            from psycopg import sql
            from psycopg.rows import tuple_row
            db.row_factory = tuple_row
            protected_tables = guard.CUT62_PROTECTED_TABLES - {'cards', 'daily_queue', 'queue_attempt_origins', 'threat_training_candidates', 'priority_repertoire_source_epochs', 'frozen_opening_runs'}
            def retained_digest():
                return {name: guard._cut62_mac(b'x' * 32, 'fixture:' + name,
                        (row[0] for row in db.execute(sql.SQL("SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text").format(sql.Identifier(name)))))
                        for name in sorted(protected_tables)}
            before = retained_digest()
            def changed_rows():
                return {name: [json.loads(row[0]) for row in db.execute(sql.SQL("SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text").format(sql.Identifier(name)))] for name in sorted(guard.CUT62_PROTECTED_TABLES - protected_tables)}
            import copy
            expected = copy.deepcopy(changed_rows())
            for name, predicate in (('cards', lambda r: r['id']=='census-exclusive'), ('daily_queue', lambda r: r['id']==900004), ('queue_attempt_origins', lambda r: r['queue_entry_id']==900004 and r['card_id']=='census-exclusive' and r['revision']==1)):
                expected[name] = [row for row in expected[name] if not predicate(row)]
            next(row for row in expected['threat_training_candidates'] if row['id']=='exclusive-candidate')['card_id'] = None
            next(row for row in expected['priority_repertoire_source_epochs'] if row['repertoire_id']=='census-rep')['version'] += 1
            old_epoch = db.execute("SELECT version FROM priority_repertoire_source_epochs WHERE repertoire_id='census-rep'").fetchone()[0]
            db.execute('SAVEPOINT exclusive_rehearsal')
            assert db.execute("UPDATE threat_training_candidates SET card_id=NULL WHERE id='exclusive-candidate' AND card_id='census-exclusive'").rowcount == 1
            assert db.execute("DELETE FROM daily_queue WHERE id=900004 AND card_id='census-exclusive'").rowcount == 1
            assert db.execute("DELETE FROM queue_attempt_origins WHERE queue_entry_id=900004 AND card_id='census-exclusive' AND revision=1 AND content_type='defense'").rowcount == 1
            assert db.execute("DELETE FROM cards WHERE id='census-exclusive' AND content_type='defense'").rowcount == 1
            assert db.execute("SELECT version FROM priority_repertoire_source_epochs WHERE repertoire_id='census-rep'").fetchone()[0] == old_epoch + 1
            assert db.execute("SELECT invalidated FROM frozen_opening_runs WHERE run_id='census-run'").fetchone()[0] == 0
            assert db.execute("SELECT card_id FROM threat_training_candidates WHERE id='exclusive-candidate'").fetchone()[0] is None
            assert retained_digest() == before
            assert changed_rows() == expected
            db.execute('ROLLBACK TO SAVEPOINT exclusive_rehearsal')
            assert retained_digest() == before
            db.row_factory = dict_row
            # An interrupted pre-cut witness must match the still-stopped source.
            from psycopg.rows import tuple_row
            db.row_factory = tuple_row
            path = tmp_path / 'defense-census-witness.json'
            guard.save_cut62_witness(path, db)
            witness, secret = guard.load_cut62_witness(path)
            assert guard.check_captured_cut62(db, witness, secret)['unchanged']
            db.execute('SAVEPOINT source_drift')
            db.execute("UPDATE cards SET state='mature' WHERE id='census-opening'")
            with pytest.raises(RuntimeError, match='source changed'):
                guard.check_captured_cut62(db, witness, secret)
            db.execute('ROLLBACK TO SAVEPOINT source_drift')
            assert guard.check_captured_cut62(db, witness, secret)['unchanged']
            # No deletion set: census-defense also appears in an unknown mixed envelope.
            # The owner must resolve each typed and JSON reference before proposing one.
        finally:
            db.rollback()


def test_cut62_existing_source_witness_check_fails_on_drift(monkeypatch):
    witness = {'version': guard.CUT62_EVIDENCE_VERSION, 'from': 61, 'to': 62, 'sentinel': 'original'}
    monkeypatch.setattr(guard, 'capture_cut62', lambda database, secret: dict(witness))
    assert guard.check_captured_cut62(None, witness, b'x' * 32)['unchanged']
    monkeypatch.setattr(guard, 'capture_cut62', lambda database, secret: {**witness, 'sentinel': 'changed'})
    with pytest.raises(RuntimeError, match='source changed'):
        guard.check_captured_cut62(None, witness, b'x' * 32)


def test_pre62_original_witness_survives_only_expected_checkpoints(monkeypatch):
    class Database:
        version = 59
        def execute(self, query):
            class Result:
                def fetchone(inner):
                    return (self.version if 'max(version)' in query else 'owner',)
            return Result()
    database = Database()
    source = guard.PRE62_SOURCE_TABLES
    layout = {name: [['id'], [['id','text',True]]] for name in source}
    state = {name: {'count':1,'digest':name} for name in source}
    monkeypatch.setattr(guard, '_pre62_session', lambda db: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda db, version: None)
    monkeypatch.setattr(guard, '_pre62_added_objects', lambda db, version, owner, acl: None)
    monkeypatch.setattr(guard, '_cut62_layout', lambda db, names: {name: layout.get(name, [['id'], []]) for name in names})
    monkeypatch.setattr(guard, '_pre62_table', lambda db, name, key, secret: state[name].copy())
    monkeypatch.setattr(guard, '_pre62_default_table_acl', lambda db: [])
    monkeypatch.setattr(guard, '_pre62_sequences', lambda db, secret: {'count':10,'digest':'seq'})
    monkeypatch.setattr(guard, '_pre62_catalog', lambda db, secret: {'count':1,'digest':'catalog'})
    witness = guard.capture_pre62_59(database, b'x'*32)
    assert 'defense_attempts' in witness['rows']
    assert 'study_position_first_responses' in witness['rows']
    for version in (59,60,61):
        database.version=version
        assert guard.verify_pre62_59(database,witness,b'x'*32)['observed_schema'] == version
    state['defense_attempts']['digest']='modified'
    with pytest.raises(RuntimeError, match='retained rows changed in defense_attempts'):
        guard.verify_pre62_59(database,witness,b'x'*32)
    state['defense_attempts']['digest']='defense_attempts'
    monkeypatch.setattr(guard, '_pre62_sequences', lambda db, secret: {'count':10,'digest':'changed'})
    with pytest.raises(RuntimeError, match='sequences changed'):
        guard.verify_pre62_59(database,witness,b'x'*32)
    database.version=62
    with pytest.raises(RuntimeError, match='unsupported pre62 target'):
        guard.verify_pre62_59(database,witness,b'x'*32)


def test_pre62_file_never_overwrites_and_rejects_other_domain(tmp_path,monkeypatch):
    witness={'domain':'pre62-source-59','version':1,'from':59,'through':61,
             'layout':dict.fromkeys(guard.PRE62_SOURCE_TABLES),'rows':dict.fromkeys(guard.PRE62_SOURCE_TABLES - {guard.CUT62_LEDGER}),
             'owner':'owner','new_table_acl':[],'catalog':{},'sequences':{}}
    monkeypatch.setattr(guard,'capture_pre62_59',lambda database,secret: dict(witness))
    path=tmp_path/'pre62.json'
    guard.save_pre62_59(path,None)
    assert path.stat().st_mode & 0o777 == 0o600
    assert guard.load_pre62_59(path)[0] == witness
    with pytest.raises(FileExistsError):
        guard.save_pre62_59(path,None)
    payload=json.loads(path.read_text())
    payload['witness']['domain']='cut62'
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError,match='authentication'):
        guard.load_pre62_59(path)


def test_pre62_native_original_59_survives_interrupted_60_and_61(tmp_path):
    """The original witness never changes across either disposable migration."""
    import os
    from urllib.parse import urlparse
    import psycopg
    from database.tooling.apply_postgres_migrations import MIGRATIONS,migration_source

    dsn=os.environ.get('TEMPO_W07_PRE62_REGRESSION_DSN')
    if not dsn:
        pytest.skip('W07 pre62 disposable database not allocated')
    parsed=urlparse(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.path != '/w07_pre62':
        raise ValueError('refusing non-W07 disposable database')
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT to_regclass('public.tempo_schema_migrations')").fetchone()[0] is None
        for path in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql')):
            if int(path.name[:3])<=59:
                db.execute(migration_source(path),prepare=False)
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('r','synthetic','synthetic','2026-01-01')")
        db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES ('opening','r','prefix','synthetic','[]','2026-01-01','opening'),('defense','r','prefix','synthetic','[]','2026-01-01','defense')")
        db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')")
        db.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('finding','game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')")
        db.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('candidate','finding','game',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','defense')")
        db.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('attempt','candidate','defense',1,'e2e4','{}','2026-01-01')")
        db.execute("INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES ('r','epoch','synthetic',now())")
        db.execute("INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at) VALUES ('r','standard','position','e2e4','1','epoch','synthetic',4503599627370496,'{}',now())")
        db.execute("INSERT INTO study_position_issued_plans(attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,owner_epoch,source_digest,issue_study_day,plan_digest,plan_json,issued_at) VALUES ('plan',900001,'opening',1,'r','standard','epoch','synthetic','2026-01-01','synthetic','{}',now())")
        db.execute("INSERT INTO study_position_first_responses(event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,payload_digest,payload_json,observed_at,accepted_at) VALUES ('first','plan',0,'r','standard','position','epoch','synthetic','{}',now(),now())")
        db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,created_at,updated_at) VALUES ('kept','synthetic','synthetic','complete','2026-01-01','2026-01-01')")
    witness_path=tmp_path/'pre62-original.json'
    with psycopg.connect(dsn) as db:
        captured_id=guard.save_pre62_59(witness_path,db)
    raw=witness_path.read_bytes()
    witness,secret=guard.load_pre62_59(witness_path)
    assert guard.authenticate_pre62_59(witness,secret)==captured_id
    import subprocess
    import sys
    result=subprocess.run([sys.executable, guard.__file__, '--pre62-authenticate59', str(witness_path)],
                          check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['witness_id']==captured_id
    def verify():
        with psycopg.connect(dsn) as db:
            result=guard.verify_pre62_59(db,witness,secret)
            assert result['witness_id']==captured_id
            return result['observed_schema']
    assert verify()==59
    with psycopg.connect(dsn) as db:
        db.execute(migration_source(MIGRATIONS/'060_position_first_response_immutable.sql'),prepare=False)
    assert verify()==60
    assert witness_path.read_bytes()==raw
    with psycopg.connect(dsn) as db:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("DELETE FROM study_position_first_responses WHERE event_id='first'")
        db.rollback()
    assert verify()==60
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE defense_attempts SET move_uci='d2d4' WHERE attempt_id='attempt'")
    with pytest.raises(RuntimeError,match='retained rows changed in defense_attempts'):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE defense_attempts SET move_uci='e2e4' WHERE attempt_id='attempt'")
        db.execute("DELETE FROM operation_receipts WHERE operation_id='kept'")
    with pytest.raises(RuntimeError,match='retained rows changed in operation_receipts'):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,created_at,updated_at) VALUES ('kept','synthetic','synthetic','complete','2026-01-01','2026-01-01')")
        db.execute("ALTER TABLE cards DISABLE TRIGGER frozen_guard_cards")
    with pytest.raises(RuntimeError,match='retained catalog changed'):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute("ALTER TABLE cards ENABLE TRIGGER frozen_guard_cards")
        db.execute('CREATE INDEX pre62_unexpected ON cards(id)')
    with pytest.raises(RuntimeError,match='retained catalog changed'):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute('DROP INDEX pre62_unexpected')
        old=db.execute('SELECT last_value,is_called FROM reviews_id_seq').fetchone()
        db.execute("SELECT setval('reviews_id_seq',5,true)")
    try:
        with pytest.raises(RuntimeError,match='retained sequences changed'):
            verify()
    finally:
        with psycopg.connect(dsn) as db:
            db.execute("SELECT setval('reviews_id_seq',%s,%s)",old)
    assert verify()==60
    with psycopg.connect(dsn) as db:
        db.execute(migration_source(MIGRATIONS/'061_content_origin_receipts.sql'),prepare=False)
    assert verify()==61
    assert witness_path.read_bytes()==raw
    with psycopg.connect(dsn) as db:
        db.execute("INSERT INTO legacy_content_historical_origins(legacy_target_id,legacy_workspace_id,export_id,receipt_id,payload,receipt_digest) VALUES ('t','w','e','r','synthetic','digest')")
    with pytest.raises(RuntimeError,match='not empty'):
        verify()


def test_cut62_witness_id_binds_retired_evidence_and_secret():
    original = {'version': guard.CUT62_EVIDENCE_VERSION, 'rows': {'retired': {'digest': 'one'}}}
    changed = {'version': guard.CUT62_EVIDENCE_VERSION, 'rows': {'retired': {'digest': 'two'}}}
    identifier = guard.cut62_witness_id(original, b'x' * 32)
    assert identifier == guard.cut62_witness_id(dict(original), b'x' * 32)
    assert identifier != guard.cut62_witness_id(changed, b'x' * 32)
    assert identifier != guard.cut62_witness_id(original, b'y' * 32)
