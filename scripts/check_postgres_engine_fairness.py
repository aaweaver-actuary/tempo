"""Native engine selection fairness with real receipts, restart and contention."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store, threat_analysis_commands, game_analysis_commands
from app.command_gateway import execute_command
from app.database import background_connection
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred


def proof_engine_fairness(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Engine fairness proof requires a disposable PostgreSQL instance')
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        now = datetime.now(timezone.utc).isoformat()
        game_id = identity + '-game'
        repertoire_id = identity + '-rep'
        database_url = fixtures.DATABASE_URL
        with postgres_store.connection() as database:
            database.execute_native("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) "
                "VALUES(%s,'lichess','proof',%s,'rapid',1,'white','1-0',%s,'[\"e2e4\"]')",
                (game_id, now, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'))
            database.execute_native('INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(%s,%s)', (game_id, now))
            database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Proof','synthetic',%s)", (repertoire_id, now))
            for index in range(6):
                request_id = f'{identity}-auto-{index}'
                database.execute_native("INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,score,evidence_json,evidence_fingerprint,created_at,updated_at) "
                    "VALUES(%s,%s,'missing_response','proof',1,'{}','proof',%s,%s)", (request_id, repertoire_id, now, now))
                database.execute_native("INSERT INTO threat_analysis_requests(id,request_json,created_at,updated_at) VALUES(%s,'{}',%s,%s)", (request_id, now, now))
                database.execute_native('INSERT INTO discovery_recommendation_requests(opportunity_id,request_id,source_game_id,source_ply,created_at,updated_at) '
                    'VALUES(%s,%s,%s,0,%s,%s)', (request_id, request_id, game_id, now, now))
        durations = []
        for index in range(3):
            postgres_store.close_pools()
            operation_id = f'{identity}-claim-{index}'
            started = time.perf_counter()
            result = execute_command(operation_id, 'threat.analysis.claim', {}, background=True)
            durations.append(time.perf_counter() - started)
            assert result['job']['id'] == f'{identity}-auto-{index}'
            assert execute_command(operation_id, 'threat.analysis.claim', {}, background=True) == result
        postgres_store.close_pools()
        with background_connection() as database:
            assert database.execute_native('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 3
            assert threat_analysis_commands.claim_threat_analysis(database, {}) == {'job': None}
        with activity_gate.foreground():
            try:
                with background_connection():
                    raise AssertionError('Foreground denial admitted an engine claim')
            except BackgroundAdmissionDeferred:
                pass
        # An interactive attempt keeps priority without consuming the ordinary turn.
        request_id = f'{identity}-auto-3'
        with postgres_store.connection() as database:
            database.execute_native("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) "
                "VALUES('proof-finding',%s,1,0,'defensive',1,'{}',%s,%s)", (game_id, now, now))
            database.execute_native("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at) "
                "VALUES('proof-candidate','proof-finding',%s,1,'proof',0,'{}','proof',1,'{}',%s,%s)", (game_id, now, now))
            database.execute_native("INSERT INTO threat_candidate_requests(candidate_id,request_id,role) VALUES('proof-candidate',%s,'attempt')", (request_id,))
        result = execute_command(identity + '-interactive', 'threat.analysis.claim', {}, background=True)
        assert result['job']['id'] == request_id
        with background_connection() as database:
            assert database.execute_native('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 3
        ordinary = execute_command(identity + '-ordinary', 'games.analysis.claim', {}, background=True)
        assert ordinary['job']['game_id'] == game_id
        assert execute_command(identity + '-ordinary', 'games.analysis.claim', {}, background=True) == ordinary
        # A locked turn yields promptly without leasing or spending a request attempt.
        with psycopg.connect(database_url) as owner:
            owner.execute('SELECT * FROM engine_scheduling_state WHERE id=1 FOR UPDATE')
            started = time.perf_counter()
            with background_connection() as database:
                assert threat_analysis_commands.claim_threat_analysis(database, {}) == {'job': None}
            assert time.perf_counter() - started < 0.25
        # Inject a crash after both changes, before commit; both must roll back.
        class Interrupted(Exception):
            pass
        try:
            with background_connection() as database:
                assert threat_analysis_commands.claim_threat_analysis(database, {})['job']
                raise Interrupted()
        except Interrupted:
            pass
        with background_connection() as database:
            assert database.execute_native('SELECT automated_streak FROM engine_scheduling_state').fetchone()[0] == 0
            assert tuple(database.execute_native('SELECT state,attempts FROM threat_analysis_requests WHERE id=%s',
                         (f'{identity}-auto-4',)).fetchone()) == ('queued', 0)
        result = execute_command(identity + '-resume', 'threat.analysis.claim', {}, background=True)
        assert result['job']['id'] == f'{identity}-auto-4'
        assert max(durations) < 0.25, durations
        print(json.dumps({'test': 'test_postgres_engine_fairness_survives_receipt_replay_restart_foreground_and_rollback',
                          'automated_limit': 3, 'ordinary_reserved': True,
                          'interactive_priority_preserved': True, 'max_claim_seconds': max(durations)}))


if __name__ == '__main__':
    proof_engine_fairness(os.environ['TEMPO_ENGINE_FAIRNESS_PROOF_URL'])
