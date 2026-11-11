"""Seed and check one synthetic Game Review on an owned CLI rehearsal stack.

Host commands (with TEMPO_TEST_INSTANCE=disposable and the development budget
owner in the environment):
  python database/tests/check_cli_game_review_survivor.py seed PROJECT
  python database/tests/check_cli_game_review_survivor.py check PROJECT
Seed once while the old schema-59 foreground worker is running, before the CLI
backup/cut. Check after start and after restart; compare the returned digest.
Only exact synthetic rows are inserted. Disposable stack teardown owns cleanup.
"""
import argparse
import json
import os
import re
import subprocess

# Generated from the existing scenario() / production PGN normalizer; identifiers
# are synthetic and fixed to support a stable pre-cut/post-restart witness.
FIXTURE = json.loads('{"identity":"cli-game-review-survivor","source_key":"cli-game-review-survivor-source","operation_id":"24112652a206f38f9274b19ceb96eedf79b8326e347c83119736320490bfd861","source":{"source_key":"cli-game-review-survivor-source","version":1,"original":{"source_game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c","pgn":"[Event \\"?\\"]\\n[Site \\"?\\"]\\n[Date \\"????.??.??\\"]\\n[Round \\"?\\"]\\n[White \\"Learner\\"]\\n[Black \\"Other\\"]\\n[Result \\"1-0\\"]\\n\\n1. d4 1-0","played_at":"2026-10-20T12:00:00Z","time_precision":"second"},"record_fingerprint":"captured-original","normalized":{"schema_version":1,"workspace_id":"legacy","game_id":"cli-game-review-survivor","game_revision":1,"source_id":"lichess","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c","starting_fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves":["d2d4"],"learner_color":"white","played_at":"2026-10-20T12:00:00Z"},"findings":[{"interface_version":"0.2.0","kind":"known_unintroduced","provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c","ply":1,"position_key":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","expected_uci":["e2e4"],"played_uci":"d2d4","repertoire_id":"rep","repertoire_revision":1,"decision_id":"cli-game-review-survivor-decision","presentation_id":"cli-game-review-survivor-card","presentation_revision":1,"introduced_before_game":false,"membership":"present","lapse_eligible":false,"diagnostic":"introduction_not_before_game","reentry_authority_id":null}],"comparison_revision":"captured-comparison","effects":[{"interface_version":"0.2.1","key":{"workspace_id":"legacy","provider":"lichess","game_id":"cli-game-review-survivor","decision_id":"cli-game-review-survivor-decision","occurrence_ply":1},"source":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"decision":{"decision_id":"cli-game-review-survivor-decision","repertoire_id":"rep","position_key":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","selected_response_uci":"e2e4","presentation_id":"cli-game-review-survivor-card","presentation_revision":1,"repertoire_revision":1,"introduction":"unknown","introduced_at":null,"available":true,"introduction_matches_current_content":true,"route_id":"alias-00","route_ply":0,"introduced_before_game":false,"introduction_game":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}},"expected_uci":"e2e4","played_uci":"d2d4","observation":"missed","detected_at":"2026-10-27T04:00:00Z","occurred_at":"2026-10-20T12:00:00Z","payload_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}],"encounters":[{"interface_version":"0.2.1","key":{"workspace_id":"legacy","provider":"lichess","game_id":"cli-game-review-survivor","decision_id":"cli-game-review-survivor-decision","occurrence_ply":1},"source":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"decision":{"decision_id":"cli-game-review-survivor-decision","repertoire_id":"rep","position_key":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","selected_response_uci":"e2e4","presentation_id":"cli-game-review-survivor-card","presentation_revision":1,"repertoire_revision":1,"introduction":"unknown","introduced_at":null,"available":true,"introduction_matches_current_content":true,"route_id":"alias-00","route_ply":0,"introduced_before_game":false,"introduction_game":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}},"expected_uci":"e2e4","played_uci":"d2d4","observation":"missed","detected_at":"2026-10-27T04:00:00Z","occurred_at":"2026-10-20T12:00:00Z","payload_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}],"quarantined":false,"diagnostic":null,"first_departure":null,"determined_at":null},"delivery":{"operation_id":"24112652a206f38f9274b19ceb96eedf79b8326e347c83119736320490bfd861","source_key":"cli-game-review-survivor-source","lapse":null,"encounter":{"interface_version":"0.2.1","key":{"workspace_id":"legacy","provider":"lichess","game_id":"cli-game-review-survivor","decision_id":"cli-game-review-survivor-decision","occurrence_ply":1},"source":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"decision":{"decision_id":"cli-game-review-survivor-decision","repertoire_id":"rep","position_key":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","selected_response_uci":"e2e4","presentation_id":"cli-game-review-survivor-card","presentation_revision":1,"repertoire_revision":1,"introduction":"unknown","introduced_at":null,"available":true,"introduction_matches_current_content":true,"route_id":"alias-00","route_ply":0,"introduced_before_game":false,"introduction_game":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}},"expected_uci":"e2e4","played_uci":"d2d4","observation":"missed","detected_at":"2026-10-27T04:00:00Z","occurred_at":"2026-10-20T12:00:00Z","payload_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"control":null,"state":"acknowledged","attempts":0,"next_attempt_at":0.0,"receipt":{"key":{"workspace_id":"legacy","provider":"lichess","game_id":"cli-game-review-survivor","decision_id":"cli-game-review-survivor-decision","occurrence_ply":1},"source_revision":1,"status":"applied","outcome":"duplicate","owner_receipt_id":"actual-owner-receipt","admitted_at":null,"available_by":null,"applied_at":null,"reason":null,"interface_version":"0.2.1","detected_at":"2026-10-27T04:00:00Z","source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c","observation":"missed","observation_recorded":true,"disposition":"first_introduction","admission_status":"available","deferred_reason":null},"error":null,"qualification_status":"pending","qualification_reason":null,"qualification_retryable":true,"qualification":null,"determined_at":null,"origin_job_id":null,"configuration_revision":null,"stream":null,"service_sequence":0,"owner_effect_id":null},"command":{"interface_version":"0.2.1","key":{"workspace_id":"legacy","provider":"lichess","game_id":"cli-game-review-survivor","decision_id":"cli-game-review-survivor-decision","occurrence_ply":1},"source":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"decision":{"decision_id":"cli-game-review-survivor-decision","repertoire_id":"rep","position_key":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -","selected_response_uci":"e2e4","presentation_id":"cli-game-review-survivor-card","presentation_revision":1,"repertoire_revision":1,"introduction":"unknown","introduced_at":null,"available":true,"introduction_matches_current_content":true,"route_id":"alias-00","route_ply":0,"introduced_before_game":false,"introduction_game":{"provider":"lichess","game_id":"cli-game-review-survivor","source_revision":1,"source_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"}},"expected_uci":"e2e4","played_uci":"d2d4","observation":"missed","detected_at":"2026-10-27T04:00:00Z","occurred_at":"2026-10-20T12:00:00Z","payload_digest":"0e93c277bb0d85e7e870dc9a05e2154c82b28bb06117e22a7fea6fa0672b5b5c"},"effect_id":"game-effect:de321df718ef12429458300154949cf4a7e13da3d4df4cf4adee6e84d2c4f7be"}')


def docker(*args, source=None):
    result = subprocess.run(['docker', *args], input=source, text=True,
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError('Owned Docker operation failed: ' + result.stderr[-2000:])
    return result.stdout


def owned(service, project, token):
    ids = docker('ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project,
                 '--filter', 'label=com.docker.compose.service=' + service).split()
    if len(ids) != 1:
        raise RuntimeError('Expected exactly one running owned ' + service)
    item, = json.loads(docker('inspect', ids[0]))
    labels = item['Config']['Labels']
    if (item['Id'] != ids[0] or not item['State']['Running']
        or labels.get('com.docker.compose.project') != project
        or labels.get('com.docker.compose.service') != service
        or labels.get('tempo.resource-class') != 'development'
        or labels.get('tempo.resource-owner') != token):
        raise RuntimeError('Disposable service identity mismatch')
    return item


COMMON = r'''
import hashlib,json,os
from urllib.parse import urlparse
import psycopg
p=json.loads(os.environ['TEMPO_CLI_GAME_FIXTURE'])
role=os.environ['TEMPO_CLI_GAME_ROLE']
assert os.environ.get('TEMPO_TEST_INSTANCE')=='disposable'
dsn=os.environ['TEMPO_DATABASE_WRITE_URL' if role=='tempo_writer' else 'TEMPO_DATABASE_READ_URL']
u=urlparse(dsn)
assert (u.hostname,u.path,u.username)==('postgres','/tempo',role)
db=psycopg.connect(dsn,connect_timeout=2)
row=db.execute("SELECT current_database(),current_user,host(inet_server_addr()),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
if row[0]!='tempo': raise RuntimeError('Owned database name differs')
if row[1]!=role: raise RuntimeError('Owned database role differs')
if row[2] not in p['postgres_addresses']: raise RuntimeError('Owned database address differs')
if row[3]!='tempo-disposable-postgres-test': raise RuntimeError('Owned database marker differs')
version=db.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0]
'''
SEED = r'''
from datetime import datetime
from app.study.interfaces.game_priority import GameEncounter,GameEffectControl
from app.study.game_feedback import Effect,effect_id
from app.study.encounter_feedback import encounter_digest
from tempo_storage.postgres import PostgresConnection
from tempo_storage.study.game_feedback import PostgresGameFeedbackStore
assert version==59
command=GameEncounter.model_validate_json(json.dumps(p['command']))
assert effect_id(command.key)==p['effect_id']
now=datetime.fromisoformat(command.detected_at.replace('Z','+00:00'))
effect=Effect(key=command.key,source_revision=command.source.source_revision,received_at=now,
 status='held',encounter=command,payload_hash=encounter_digest(command),reason='reanalysis',
 disposition='first_introduction',admission_status='pending',deferred_reason='authority_unavailable',
 observation_recorded=True,control=GameEffectControl(key=command.key,source_revision=command.source.source_revision,action='hold',reason='reanalysis'))
db.execute("SET LOCAL statement_timeout='3s'")
db.execute('INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s::jsonb)',(p['source_key'],json.dumps(p['source'])))
db.execute("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'encounter','acknowledged',0,%s::jsonb)",(p['operation_id'],p['source_key'],command.key.logical_id(),json.dumps(p['delivery'])))
PostgresGameFeedbackStore(PostgresConnection(db)).save(effect,now)
db.commit()
print(json.dumps({'seeded':True,'schema':version}))
'''
CHECK = r'''
from urllib.request import urlopen
from io import StringIO
import chess,chess.pgn
assert version==62
assert db.execute('SHOW transaction_read_only').fetchone()[0]=='on'
def snapshot():
 return [db.execute(sql,(value,)).fetchone()[0] for sql,value in (
 ('SELECT payload::text FROM product_feedback_sources WHERE source_key=%s',p['source_key']),
 ('SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s',p['operation_id']),
 ('SELECT row_to_json(f)::text FROM study_game_feedback f WHERE effect_key=%s',p['effect_id']))]
before=snapshot()
def get(path):
 with urlopen('http://127.0.0.1:8000'+path,timeout=5) as response:
  assert response.status==200
  return json.load(response)
games=get('/api/product/game-review/games')['games']
assert any(g['provider']=='lichess' and g['game_id']==p['identity'] for g in games)
detail=get('/api/product/game-review/games/lichess/'+p['identity'])
raw=p['source']['original']['pgn']
assert hashlib.sha256(raw.encode()).hexdigest()==p['source']['original']['source_digest']
game=chess.pgn.read_game(StringIO(raw)); assert game is not None and not game.errors
board=game.board();assert board.fen()==detail['starting_fen']
played=[]
for move in game.mainline_moves():
 assert move in board.legal_moves;played.append(move.uci());board.push(move)
assert played==detail['moves'] and played
receipt,=detail['receipts']
assert receipt['key']==p['command']['key'] and receipt['owner_receipt_id']==p['effect_id']
assert receipt['status']=='held' and receipt['disposition']=='first_introduction'
assert receipt['admission_status']=='pending' and receipt['deferred_reason']=='authority_unavailable'
assert receipt['source_digest']==p['command']['source']['source_digest']
assert snapshot()==before
print(json.dumps({'schema':version,'legal_moves':len(played),'owner_receipt':'held','fixture_sha256':hashlib.sha256(json.dumps(before,separators=(',',':')).encode()).hexdigest()}))
db.rollback()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['seed', 'check'])
    parser.add_argument('project')
    args = parser.parse_args()
    token = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_TOKEN', '')
    if (os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable' or not token
        or not re.fullmatch(r'tempo-pg-regressions-[a-z0-9-]+-cli', args.project)):
        raise RuntimeError('Explicit disposable CLI project and development owner required')
    postgres = owned('postgres', args.project, token)
    service = owned('foreground-worker' if args.mode == 'seed' else 'api', args.project, token)
    if args.mode == 'check':
        for mount in service['Mounts']:
            dest = mount['Destination'].rstrip('/')
            if any(dest == root or dest.startswith(root + '/') for root in ('/app','/packages','/contracts','/services')):
                raise RuntimeError('Packaged API must not have a source overlay')
    addresses = [network['IPAddress'] for network in postgres['NetworkSettings']['Networks'].values()]
    if not addresses or not all(addresses):
        raise RuntimeError('Owned PostgreSQL address unavailable')
    payload = {**FIXTURE, 'postgres_addresses': addresses}
    role = 'tempo_writer' if args.mode == 'seed' else 'tempo_reader'
    result = docker('exec', '-i', '--env', 'TEMPO_TEST_INSTANCE=disposable',
        '--env', 'TEMPO_CLI_GAME_ROLE='+role, '--env', 'TEMPO_CLI_GAME_FIXTURE='+json.dumps(payload,separators=(',',':')),
        service['Id'], 'python', '-', source=COMMON + (SEED if args.mode == 'seed' else CHECK))
    print(result.strip().splitlines()[-1])


if __name__ == '__main__':
    main()
