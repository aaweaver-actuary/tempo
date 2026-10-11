"""Owned PostgreSQL engine claims, durable replay, process crash and bounded fairness."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import sys
from threading import Event
import time
from unittest.mock import patch

import chess
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store, threat_analysis_commands, game_analysis_commands
from app.schema_version import POSTGRES_SCHEMA_VERSION
from app.command_gateway import execute_command
from app.database import background_connection
from app.services import engine_scheduling, redis_admission_gate
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.threat_pipeline import ENGINE_VERSION, NETWORK_VERSION
from app.services.threat_validation import AnalysisRequest, AnalysisReport, AnalysisLine, EngineScore


class EngineFairnessProof:
    def __init__(self, identity):
        self.identity = identity
        self.database_url = os.environ['TEMPO_DATABASE_WRITE_URL']
        self.game_id = identity + '-game'
        self.request_ids = []
        self.sequence = 0
        self.durations = []
        self.results = []

    def sql(self, statement, parameters=()):
        with psycopg.connect(self.database_url) as database:
            cursor = database.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []

    def seed(self):
        now = datetime.now(timezone.utc).isoformat()
        self.sql("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) "
                 "VALUES(%s,'lichess','proof',%s,'rapid',1,'white','1-0',%s,'[\"e2e4\"]')",
                 (self.game_id, now, chess.STARTING_FEN))
        self.sql('INSERT INTO game_analysis_jobs(game_id,analysis_evidence_version,updated_at) VALUES(%s,3,%s)', (self.game_id, now))
        self.sql("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Proof','synthetic',%s)", (self.identity, now))
        for index, move in enumerate(list(chess.Board().legal_moves)[:12]):
            request = AnalysisRequest(chess.STARTING_FEN, (move.uci(),), ENGINE_VERSION, NETWORK_VERSION, 8, 1)
            request_id = request.request_id
            self.request_ids.append(request_id)
            self.sql("INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,score,evidence_json,evidence_fingerprint,created_at,updated_at) "
                     "VALUES(%s,%s,'missing_response',%s,1,'{}','proof',%s,%s)", (request_id, self.identity, str(index), now, now))
            self.sql('INSERT INTO threat_analysis_requests(id,request_json,created_at,updated_at) VALUES(%s,%s,%s,%s)',
                     (request_id, json.dumps(asdict(request)), f'2026-10-01T00:00:{index:02}+00:00', now))
            self.sql('INSERT INTO discovery_recommendation_requests(opportunity_id,request_id,source_game_id,source_ply,created_at,updated_at) '
                     'VALUES(%s,%s,%s,0,%s,%s)', (request_id, request_id, self.game_id, now, now))
        self.sql("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) "
                 "VALUES(%s,%s,1,0,'defensive',1,'{}',%s,%s)", (self.identity, self.game_id, now, now))
        self.sql("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at) "
                 "VALUES(%s,%s,%s,1,'proof',0,'{}','proof',1,'{}',%s,%s)", (self.identity, self.identity, self.game_id, now, now))

    def reset(self, streak=0):
        self.sql('DELETE FROM threat_candidate_requests WHERE candidate_id=%s', (self.identity,))
        self.sql('DELETE FROM background_activity')
        self.sql('DELETE FROM game_analysis_position_reports')
        self.sql("UPDATE imported_games SET rated=1,speed='rapid',analysis_version=1")
        self.sql("UPDATE game_analysis_jobs SET status='queued',analysis_version=1,lease_id=NULL,lease_expires_at=NULL,attempts=0")
        self.sql("UPDATE threat_analysis_requests SET state='queued',lease_id=NULL,lease_expires_at=NULL,attempts=0")
        self.sql('UPDATE engine_scheduling_state SET automated_streak=%s', (streak,))

    def streak(self):
        return self.sql('SELECT automated_streak FROM engine_scheduling_state')[0][0]

    def operation(self, command, payload, operation_id=None):
        self.sequence += 1
        operation_id = operation_id or f'{self.identity}-operation-{self.sequence}'
        started = time.perf_counter()
        result = execute_command(operation_id, command, payload, background=True)
        self.durations.append(time.perf_counter() - started)
        return result

    def automated(self):
        return self.operation('threat.analysis.claim', {})

    def ordinary(self, plan=None):
        plan = plan or game_analysis_commands.prepare_position_claim(self.game_id)
        assert plan is not None
        return self.operation('games.analysis.position.claim', plan)

    def interactive(self):
        self.sql("INSERT INTO threat_candidate_requests(candidate_id,request_id,role) VALUES(%s,%s,'attempt')",
                 (self.identity, self.request_ids[0]))

    @contextmanager
    def transaction(self, raw=None):
        # Separate PostgreSQL clients exercise claim serialization independently of Redis's admission gate.
        if raw is None:
            with psycopg.connect(self.database_url, autocommit=True, row_factory=postgres_store.tempo_row_factory) as connection:
                with self.transaction(connection) as database:
                    yield database
            return
        with raw.transaction():
            raw.execute("SET LOCAL transaction_timeout='250ms'")
            raw.execute("SET LOCAL lock_timeout='25ms'")
            database = postgres_store.PostgresConnection(raw)
            yield database
            database.flush_background_metrics()

    def test_postgres_engine_three_accepted_claims_reserve_production_position(self):
        self.reset()
        for index in range(3):
            assert self.automated()['job']['id'] == self.request_ids[index]
            assert self.streak() == index + 1
        assert self.automated() == {'job': None, 'preferred_game_id': self.game_id}
        assert self.ordinary()['job']['kind'] == 'search'
        assert self.streak() == 0
        assert self.automated()['job']['id'] == self.request_ids[3]

    def test_postgres_engine_interactive_bypasses_reserved_and_locked_turn(self):
        self.reset(3)
        self.interactive()
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        assert self.ordinary(plan) == {'job': None}
        assert self.streak() == 3
        with psycopg.connect(self.database_url) as owner:
            owner.execute('SELECT * FROM engine_scheduling_state WHERE id=1 FOR UPDATE')
            assert self.automated()['job']['id'] == self.request_ids[0]
        assert self.streak() == 3
        assert self.ordinary(plan)['job']

    def test_postgres_engine_unavailable_paused_deleted_and_stale_games_do_not_block(self):
        for unavailable in ('paused', 'leased', 'ineligible', 'absent'):
            self.reset(3)
            if unavailable == 'paused':
                self.sql("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('game_analysis',%s,1,%s)",
                         (self.game_id, datetime.now(timezone.utc).isoformat()))
            elif unavailable == 'leased':
                self.sql("UPDATE game_analysis_jobs SET status='leased',lease_expires_at='2099-01-01'")
            elif unavailable == 'ineligible':
                self.sql('UPDATE imported_games SET rated=0')
            else:
                self.sql("UPDATE game_analysis_jobs SET status='complete'")
            assert self.automated()['job']
            assert self.streak() == 3
        self.reset(3)
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        plan['analysis_version'] += 1
        assert self.ordinary(plan) == {'job': None}
        plan['game_id'] = 'deleted-game'
        assert self.ordinary(plan) == {'job': None}
        assert self.streak() == 3
        assert game_analysis_commands.prepare_position_claim('deleted-game') is None

    def test_postgres_engine_locked_game_and_stale_hint_allow_next_automated_claim(self):
        self.reset(3)
        with psycopg.connect(self.database_url) as owner:
            owner.execute('SELECT * FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE', (self.game_id,))
            assert self.automated()['job']
        with psycopg.connect(self.database_url) as owner:
            owner.execute('SELECT * FROM imported_games WHERE id=%s FOR UPDATE', (self.game_id,))
            assert self.automated()['job']
            assert self.streak() == 3
        reservation = self.automated()
        assert reservation['preferred_game_id'] == self.game_id
        plan = game_analysis_commands.prepare_position_claim(reservation['preferred_game_id'])
        self.sql("UPDATE game_analysis_jobs SET status='complete'")
        assert self.ordinary(plan) == {'job': None}
        assert self.streak() == 3
        assert self.automated()['job']

    def test_postgres_engine_failed_claim_and_handler_rollback_preserve_streak(self):
        self.reset()
        self.sql("CREATE FUNCTION reject_engine_claim() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END $$")
        self.sql("CREATE TRIGGER reject_engine_claim BEFORE UPDATE ON threat_analysis_requests FOR EACH ROW "
                 "WHEN (NEW.state='leased') EXECUTE FUNCTION reject_engine_claim()")
        try:
            assert self.automated() == {'job': None}
            assert self.streak() == 0
        finally:
            self.sql('DROP TRIGGER reject_engine_claim ON threat_analysis_requests')
            self.sql('DROP FUNCTION reject_engine_claim()')
        original = threat_analysis_commands.record_automated_selection
        def fail_after_transition(database):
            original(database)
            raise ValueError('Injected handler rollback')
        with patch.object(threat_analysis_commands, 'record_automated_selection', fail_after_transition):
            assert self.automated() is None
        assert self.streak() == 0
        assert self.sql('SELECT state,attempts FROM threat_analysis_requests WHERE id=%s', (self.request_ids[0],))[0] == ('queued', 0)
        assert self.automated()['job']['id'] == self.request_ids[0]
        self.reset(3)
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        original_reset = game_analysis_commands.record_ordinary_selection
        def fail_ordinary(database):
            original_reset(database)
            raise ValueError('Injected ordinary rollback')
        with patch.object(game_analysis_commands, 'record_ordinary_selection', fail_ordinary):
            assert self.ordinary(plan) is None
        assert self.streak() == 3
        assert self.sql('SELECT status,attempts FROM game_analysis_jobs')[0] == ('queued', 0)

    def test_postgres_engine_rejected_parent_and_position_writes_do_not_spend_turn(self):
        from app.command_gateway import read_operation
        self.sql("CREATE FUNCTION reject_game_write() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NULL; END$$")
        try:
            for table, event in [('game_analysis_jobs', 'UPDATE'), ('game_analysis_position_reports', 'INSERT')]:
                self.reset(3)
                plan = game_analysis_commands.prepare_position_claim(self.game_id)
                self.sql(f'CREATE TRIGGER reject_game_write BEFORE {event} ON {table} FOR EACH ROW EXECUTE FUNCTION reject_game_write()')
                try:
                    if table == 'game_analysis_jobs':
                        assert self.ordinary(plan) == {'job': None}
                    else:
                        operation_id = self.identity + '-rejected-position'
                        assert self.operation('games.analysis.position.claim', plan, operation_id) is None
                        receipt = read_operation(operation_id, background=True)
                        assert receipt['state'] == 'failed'
                        assert receipt['error']['status_code'] == 409
                    assert self.streak() == 3
                    assert self.sql('SELECT status,attempts FROM game_analysis_jobs')[0] == ('queued', 0)
                finally:
                    self.sql(f'DROP TRIGGER reject_game_write ON {table}')
        finally:
            self.sql('DROP FUNCTION reject_game_write()')

    def test_postgres_engine_committed_selection_survives_real_failure_and_release(self):
        self.reset()
        for command, expected in [('threat.analysis.release', 'queued'), ('threat.analysis.failure', 'retrying')]:
            selected = self.automated()['job']
            streak_before_callback = self.streak()
            payload = {'request_id': selected['id'], 'lease_id': selected['lease_id'], 'error': 'Synthetic search failure'}
            assert self.operation(command, payload)['status'] == expected
            assert self.streak() == streak_before_callback
        self.sql('UPDATE engine_scheduling_state SET automated_streak=3')
        ordinary = self.ordinary()['job']
        assert self.streak() == 0
        assert self.operation('games.analysis.position.release',
            {'report_id': ordinary['id'], 'lease_id': ordinary['lease_id']})['status'] == 'queued'
        assert self.streak() == 0

    def test_postgres_engine_concurrent_claims_cannot_spend_same_reserved_turn(self):
        self.reset(3)
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        owner_accepted = Event()
        contender_finished = Event()
        # Open both clients before starting either bounded transaction.
        with psycopg.connect(self.database_url, autocommit=True, row_factory=postgres_store.tempo_row_factory) as owner_connection, \
             psycopg.connect(self.database_url, autocommit=True, row_factory=postgres_store.tempo_row_factory) as contender_connection:
            def owner_claim():
                with self.transaction(owner_connection) as database:
                    result = game_analysis_commands.claim_game_analysis_position(database, plan)
                    assert result['job']
                    owner_accepted.set()
                    assert contender_finished.wait(0.15), 'Concurrent contender did not yield within the transaction budget'
                    return result
            def contender_claim():
                assert owner_accepted.wait(2)
                try:
                    with self.transaction(contender_connection) as database:
                        return game_analysis_commands.claim_game_analysis_position(database, plan)
                finally:
                    contender_finished.set()
            with ThreadPoolExecutor(max_workers=2) as workers:
                first = workers.submit(owner_claim)
                second = workers.submit(contender_claim)
                assert second.result(timeout=3) == {'job': None}
                assert first.result(timeout=3)['job']
        assert self.streak() == 0
        assert self.sql('SELECT attempts FROM game_analysis_jobs')[0][0] == 1
        self.reset(2)
        # Connection setup must finish before either bounded transaction starts.
        with psycopg.connect(self.database_url, autocommit=True, row_factory=postgres_store.tempo_row_factory) as owner_connection, \
             psycopg.connect(self.database_url, autocommit=True, row_factory=postgres_store.tempo_row_factory) as contender_connection:
            with self.transaction(owner_connection) as owner:
                assert threat_analysis_commands.claim_threat_analysis(owner, {})['job']
                with self.transaction(contender_connection) as contender:
                    assert threat_analysis_commands.claim_threat_analysis(contender, {}) == {'job': None}
        assert self.streak() == 3
        assert self.automated()['preferred_game_id'] == self.game_id

    def test_postgres_engine_receipt_replay_does_not_increment_or_reset_twice(self):
        self.reset()
        operation_id = self.identity + '-automated-replay'
        result = self.operation('threat.analysis.claim', {}, operation_id)
        assert self.operation('threat.analysis.claim', {}, operation_id) == result
        assert self.streak() == 1
        self.sql('UPDATE engine_scheduling_state SET automated_streak=3')
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        ordinary_operation = self.identity + '-ordinary-replay'
        result = self.operation('games.analysis.position.claim', plan, ordinary_operation)
        assert self.streak() == 0
        assert self.automated()['job']
        assert self.operation('games.analysis.position.claim', plan, ordinary_operation) == json.loads(json.dumps(result))
        assert self.streak() == 1
        from app import main
        from app.models import GameAnalysisPositionClaimRequest
        assert main.claim_game_analysis_position(GameAnalysisPositionClaimRequest(preferred_game_id=self.game_id),
            engine_worker='docker', idempotency_key=ordinary_operation) == json.loads(json.dumps(result))
        assert self.streak() == 1

    def assert_position_claim_request_identity(self, pending):
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from app import command_dispatch, main
        from app.command_gateway import record_operation_attempt
        from app.models import GameAnalysisPositionClaimRequest

        for original_request in ({'preferred_game_id': self.game_id}, {}):
            self.reset(streak=3)
            self.sequence += 1
            operation_id = f'{self.identity}-position-identity-{self.sequence}'

            def deliver(command_name, payload, *, idempotency_key, background):
                assert idempotency_key == operation_id and background
                with activity_gate.background_control():
                    admitted, saved_payload, attempt_token, _ = record_operation_attempt(
                        operation_id, command_name, payload, background=True)
                assert admitted and saved_payload['claim_request'] == original_request
                if pending:
                    self.sql("UPDATE operation_receipts SET state='queued',attempt_token=NULL,lease_expires_at=NULL "
                             "WHERE operation_id=%s", (operation_id,))
                    return JSONResponse(status_code=202, content={'operation_id': operation_id, 'state': 'queued'})
                return execute_command(operation_id, command_name, saved_payload,
                                       background=True, attempt_token=attempt_token)

            request = GameAnalysisPositionClaimRequest(**original_request)
            with patch.object(command_dispatch, 'dispatch_command', deliver):
                original_response = main.claim_game_analysis_position(
                    request, engine_worker='docker', idempotency_key=operation_id)
            assert self.streak() == (3 if pending else 0)
            original_receipt = self.sql('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation_id,))
            original_jobs = self.sql('SELECT * FROM game_analysis_jobs ORDER BY game_id')
            original_positions = self.sql('SELECT * FROM game_analysis_position_reports ORDER BY id')
            conflicting_requests = ([{'preferred_game_id': 'different-game'}, {}] if original_request
                                    else [{'preferred_game_id': self.game_id}])
            postgres_store.close_pools()
            with patch.object(game_analysis_commands, 'prepare_position_claim',
                              side_effect=AssertionError('Receipt recovery prepared new work')), \
                 patch.object(command_dispatch, 'dispatch_command',
                              side_effect=AssertionError('Receipt recovery dispatched new work')):
                replay = main.claim_game_analysis_position(request, engine_worker='docker', idempotency_key=operation_id)
                if pending:
                    assert replay.status_code == 202 and replay.body == original_response.body
                else:
                    assert replay == json.loads(json.dumps(original_response))
                for conflicting_request in conflicting_requests:
                    try:
                        main.claim_game_analysis_position(GameAnalysisPositionClaimRequest(**conflicting_request),
                            engine_worker='docker', idempotency_key=operation_id)
                    except HTTPException as error:
                        assert error.status_code == 409
                        assert error.detail == 'Operation ID was already used for another request'
                    else:
                        raise AssertionError('Changed position-claim payload reused the original operation')
            assert self.sql('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation_id,)) == original_receipt
            assert self.sql('SELECT * FROM game_analysis_jobs ORDER BY game_id') == original_jobs
            assert self.sql('SELECT * FROM game_analysis_position_reports ORDER BY id') == original_positions
            assert self.streak() == (3 if pending else 0)
            if pending:
                saved_payload = json.loads(self.sql('SELECT payload_json FROM operation_receipts WHERE operation_id=%s',
                                                   (operation_id,))[0][0])
                with activity_gate.background_control():
                    admitted, saved_payload, attempt_token, _ = record_operation_attempt(
                        operation_id, 'games.analysis.position.claim', saved_payload, background=True)
                assert admitted
                recovered_response = execute_command(operation_id, 'games.analysis.position.claim', saved_payload,
                                                     background=True, attempt_token=attempt_token)
                assert recovered_response['job'] and self.streak() == 0
                assert main.claim_game_analysis_position(request, engine_worker='docker',
                                                        idempotency_key=operation_id) == json.loads(json.dumps(recovered_response))
                assert self.streak() == 0

    def test_postgres_engine_completed_position_receipt_validates_request_identity(self):
        self.assert_position_claim_request_identity(pending=False)

    def test_postgres_engine_pending_position_receipt_validates_request_identity(self):
        self.assert_position_claim_request_identity(pending=True)

    def child(self, mode):
        environment = dict(os.environ, TEMPO_ENGINE_FAIRNESS_CHILD_MODE=mode,
                           TEMPO_ENGINE_FAIRNESS_FOREGROUND_KEY=redis_admission_gate._FOREGROUND_KEY,
                           TEMPO_ENGINE_FAIRNESS_BACKGROUND_KEY=redis_admission_gate._BACKGROUND_KEY)
        child = subprocess.run([sys.executable, __file__], env=environment, capture_output=True, text=True, timeout=10)
        assert child.returncode == (17 if mode == 'crash' else 0), child.stderr
        return child

    def test_postgres_engine_restart_recovers_correct_next_selection(self):
        self.reset(2)
        assert json.loads(self.child('claim').stdout)['job']['id'] == self.request_ids[0]
        postgres_store.close_pools()
        assert self.streak() == 3
        assert self.automated()['preferred_game_id'] == self.game_id

    def test_postgres_engine_process_crash_rolls_back_uncommitted_selection(self):
        self.reset(2)
        self.child('crash')
        assert self.streak() == 2
        assert self.sql('SELECT state,attempts FROM threat_analysis_requests WHERE id=%s', (self.request_ids[0],))[0] == ('queued', 0)
        assert self.automated()['job']['id'] == self.request_ids[0]
        assert self.automated()['preferred_game_id'] == self.game_id

    def test_postgres_engine_foreground_and_turn_row_contention_remain_bounded(self):
        self.reset(3)
        with activity_gate.foreground():
            try:
                self.automated()
            except BackgroundAdmissionDeferred:
                pass
            else:
                raise AssertionError('Foreground admission permitted a discretionary engine claim')
        assert self.streak() == 3
        with psycopg.connect(self.database_url) as owner:
            owner.execute('SELECT * FROM engine_scheduling_state WHERE id=1 FOR UPDATE')
            with background_connection() as database:
                assert threat_analysis_commands.claim_threat_analysis(database, {}) == {'job': None}
            assert self.sql('SELECT count(*) FROM threat_analysis_requests WHERE attempts<>0')[0][0] == 0
        assert self.automated()['preferred_game_id'] == self.game_id

    def test_postgres_engine_promotions_and_existing_automated_order_are_preserved(self):
        self.reset()
        self.sql("INSERT INTO background_activity(source,work_id,promoted,updated_at) VALUES('threat_analysis',%s,1,%s)",
                 (self.request_ids[2], datetime.now(timezone.utc).isoformat()))
        expected = (self.request_ids[2], self.request_ids[0], self.request_ids[1])
        for index, request_id in enumerate(expected):
            assert self.automated()['job']['id'] == request_id
            assert self.streak() == index+1
        assert self.automated()['preferred_game_id'] == self.game_id

    @staticmethod
    def report(request_json):
        request = AnalysisRequest(**{**request_json, 'position_prefix_uci': tuple(request_json['position_prefix_uci'])})
        board = chess.Board(request.position_start_fen)
        for move in request.position_prefix_uci:
            board.push_uci(move)
        roots = [request.root_move_uci] if request.root_move_uci else [move.uci() for move in list(board.legal_moves)[:request.multipv]]
        return asdict(AnalysisReport(request,
            tuple(AnalysisLine(move, (move,), EngineScore(cp=0), request.depth) for move in roots), True))

    def complete_position(self, job):
        report = self.report(job['request'])
        request_json = game_analysis_commands.prepare_position_report(job['id'], report)
        return self.operation('games.analysis.position.report', {'report_id': job['id'], 'lease_id': job['lease_id'],
                                                               'request_json': request_json, 'report': report})

    def test_postgres_engine_terminal_checkpoint_and_finalization_reset_only_after_acceptance(self):
        self.reset(3)
        terminal_fen = '7k/6Q1/6K1/8/8/8/8/8 b - - 0 1'
        self.sql("UPDATE imported_games SET start_fen=%s,moves_json='[]'", (terminal_fen,))
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        assert plan['kind'] == 'terminal'
        assert self.ordinary(plan) == {'job': None}
        assert self.streak() == 0
        self.sql('UPDATE engine_scheduling_state SET automated_streak=3')
        plan = game_analysis_commands.prepare_position_claim(self.game_id)
        assert plan['kind'] == 'finalize'
        assert self.ordinary(plan)['job']['kind'] == 'finalize'
        assert self.streak() == 0
        self.sql('UPDATE imported_games SET start_fen=%s,moves_json=%s', (chess.STARTING_FEN, '["e2e4"]'))

    def test_postgres_engine_upgrade_from_schema_44_preserves_historical_data(self):
        import apply_postgres_migrations as migration
        upgrade_name = self.identity + '_v44'
        upgrade_url = make_conninfo(self.database_url, dbname=upgrade_name)
        with psycopg.connect(self.database_url, autocommit=True) as parent:
            parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(upgrade_name)))
        try:
            with TemporaryDirectory(prefix='tempo-pr123-v44-') as directory:
                historical_migrations = Path(directory)
                for source in migration.MIGRATIONS.glob('*.sql'):
                    if int(source.name[:3]) <= 44:
                        (historical_migrations / source.name).symlink_to(source)
                with patch.object(migration, 'MIGRATIONS', historical_migrations), patch.object(migration, 'POSTGRES_SCHEMA_VERSION', 44):
                    migration.apply_migrations(upgrade_url)
            with psycopg.connect(upgrade_url) as database:
                database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) "
                    "VALUES('historical','lichess','learner','2026-01-01','rapid',1,'white','1-0',%s,'[\"e2e4\"]')", (chess.STARTING_FEN,))
            migration.apply_migrations(upgrade_url)
            with psycopg.connect(upgrade_url) as database:
                assert database.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 0
                database.execute('UPDATE engine_scheduling_state SET automated_streak=3')
            migration.apply_migrations(upgrade_url)
            with psycopg.connect(upgrade_url) as database:
                assert database.execute('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 3
                assert database.execute('SELECT id,moves_json FROM imported_games').fetchall() == [('historical', '["e2e4"]')]
                assert [row[0] for row in database.execute('SELECT version FROM tempo_schema_migrations ORDER BY version')] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
        finally:
            with psycopg.connect(self.database_url, autocommit=True) as parent:
                parent.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(upgrade_name)))

    def test_postgres_engine_migration_replay_preserves_streak_and_historical_game(self):
        from apply_postgres_migrations import apply_migrations
        self.reset(3)
        before = self.sql('SELECT id,moves_json,analysis_version FROM imported_games')
        apply_migrations(self.database_url)
        apply_migrations(self.database_url)
        assert self.streak() == 3
        assert self.sql('SELECT id,moves_json,analysis_version FROM imported_games') == before
        assert [row[0] for row in self.sql('SELECT version FROM tempo_schema_migrations ORDER BY version')] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))

    def test_postgres_engine_mixed_backlog_has_bounded_service_and_inspectable_plans(self):
        self.reset()
        observations = []
        ordinary_completions = 0
        automated_completions = 0
        finalization_leases = 0
        ordinary_age = float(self.sql("SELECT EXTRACT(EPOCH FROM (NOW()-MIN(updated_at::timestamptz))) FROM game_analysis_jobs WHERE status='queued'")[0][0])
        # Actual validated callbacks complete positions; searches themselves are deterministic synthetic evidence.
        for _ in range(12):
            selected = self.automated()
            if selected['job']:
                job = selected['job']
                report = self.report(job['request'])
                assert self.operation('threat.analysis.report', {'request_id': job['id'], 'lease_id': job['lease_id'], 'report': report})['status'] == 'complete'
                automated_completions += 1
                observations.append('automated')
            else:
                job = self.ordinary()['job']
                assert job
                if job['kind'] == 'search':
                    assert self.complete_position(job)['status'] == 'complete'
                    ordinary_completions += 1
                else:
                    assert job['kind'] == 'finalize'
                    finalization_leases += 1
                observations.append('ordinary')
        assert observations == ['automated', 'automated', 'automated', 'ordinary'] * 3, observations
        now = datetime.now(timezone.utc).isoformat()
        queries = {
            'ordinary': ('SELECT j.game_id ' + engine_scheduling.ORDINARY_GAME_ELIGIBILITY_SQL.replace('?', '%s') +
                         engine_scheduling.ORDINARY_GAME_ORDER_SQL + 'LIMIT 1 FOR UPDATE OF j,g SKIP LOCKED', (now,)),
            'automated': (engine_scheduling.ELIGIBLE_THREAT_REQUEST_SQL +
                          "AND COALESCE(control.promoted,0)=0 ORDER BY request.created_at,request.id LIMIT 1 FOR UPDATE OF request SKIP LOCKED", ()),
        }
        self.sql("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) "
                 "SELECT 'plan-game-'||number,'lichess','fixture','2000-01-01','rapid',1,'white','1-0',%s,'[]' "
                 "FROM generate_series(1,3000) number", (chess.STARTING_FEN,))
        self.sql("INSERT INTO game_analysis_jobs(game_id,updated_at) SELECT id,'2000-01-01' FROM imported_games WHERE id LIKE 'plan-game-%%'")
        self.sql("INSERT INTO threat_analysis_requests(id,request_json,created_at,updated_at) "
                 "SELECT 'plan-request-'||number,'{}','2099-01-01','2099-01-01' FROM generate_series(1,3000) number")
        self.sql('ANALYZE game_analysis_jobs')
        self.sql('ANALYZE imported_games')
        self.sql('ANALYZE threat_analysis_requests')
        plans = {}
        with psycopg.connect(self.database_url) as database:
            for label, (statement, parameters) in queries.items():
                plans[label] = database.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + statement, parameters).fetchone()[0]
            database.rollback()
        self.sql("DELETE FROM imported_games WHERE id LIKE 'plan-game-%%'")
        self.sql("DELETE FROM threat_analysis_requests WHERE id LIKE 'plan-request-%%'")
        # Restore statistics after removing the large plan fixture; later small
        # claim cases must not inherit its JIT threshold or dead-row estimates.
        for table in ('game_analysis_jobs', 'imported_games', 'threat_analysis_requests'):
            self.sql('ANALYZE ' + table)
        oldest = self.sql("SELECT EXTRACT(EPOCH FROM (NOW()-MIN(created_at::timestamptz))) FROM threat_analysis_requests WHERE state='queued'")[0][0]
        self.results.append({'selection_sequence': observations, 'automated_completed': automated_completions,
                             'ordinary_positions_completed': ordinary_completions, 'finalization_leases_accepted': finalization_leases, 'oldest_eligible_automated_seconds': float(oldest),
                             'oldest_eligible_ordinary_seconds_before_service': ordinary_age,
                             'oldest_eligible_ordinary_seconds_after_service': None,
                             'plans': plans, 'measurement': 'Selection bound, not equal CPU time; synthetic validated engine reports'})


def proof_engine_fairness(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Engine fairness proof requires an explicitly disposable PostgreSQL instance')
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        proof = EngineFairnessProof(identity)
        proof.seed()
        for name in sorted(name for name in dir(proof) if name.startswith('test_postgres_engine_')):
            getattr(proof, name)()
            print('PASS ' + name, flush=True)
        print(json.dumps({'engine_fairness': proof.results, 'maximum_claim_callback_seconds': max(proof.durations),
                          'transaction_budget_ms': 250, 'lock_budget_ms': 25}), flush=True)


if __name__ == '__main__':
    if child_mode := os.getenv('TEMPO_ENGINE_FAIRNESS_CHILD_MODE'):
        redis_admission_gate._FOREGROUND_KEY = os.environ['TEMPO_ENGINE_FAIRNESS_FOREGROUND_KEY']
        redis_admission_gate._BACKGROUND_KEY = os.environ['TEMPO_ENGINE_FAIRNESS_BACKGROUND_KEY']
        if child_mode == 'crash':
            with postgres_store.connection(background=True) as database:
                assert threat_analysis_commands.claim_threat_analysis(database, {})['job']
                os._exit(17)
        print(json.dumps(execute_command('process-restart-claim', 'threat.analysis.claim', {}, background=True)))
    else:
        proof_engine_fairness(os.environ['TEMPO_ENGINE_FAIRNESS_PROOF_URL'])
