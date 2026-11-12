"""One packaged reader proof against an ALREADY owned disposable product stack.

Run with the existing validation lock and explicit TEMPO_TEST_INSTANCE=disposable,
TEMPO_TEST_COMPOSE_PROJECT=tempo-pg-regressions-<digits>-<hex>, and that stack's
TEMPO_DEVELOPMENT_BUDGET_TOKEN. API, foreground-worker and PostgreSQL must already
be running with the current schema and provisioned reader/writer profiles. Nothing here
creates a container, credential, role, grant, image, or application instance.
Only three uniquely named synthetic game/repertoire/line rows are seeded/removed by the
existing writer. Import performs no Docker, HTTP, database or fixture work.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import pytest

from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = {
    "backend/app/main.py": "/app/app/main.py",
    "backend/app/games/review.py": "/app/app/games/review.py",
    "backend/app/games/comparison.py": "/app/app/games/comparison.py",
    "database/tempo_storage/game_review.py": "/app/tempo_storage/game_review.py",
    "database/tempo_storage/connections.py": "/app/tempo_storage/connections.py",
    "database/tempo_storage/ddl/schema_version.py": "/app/tempo_storage/ddl/schema_version.py",
}
READ_RELATIONS = ("imported_games", "repertoire_lines", "repertoires")


def _docker(*args, source=None):
    result = subprocess.run(["docker", *args], input=source, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, "Owned Docker operation failed:\n" + result.stderr
    return result.stdout


def _owned_containers():
    project = os.environ.get("TEMPO_TEST_COMPOSE_PROJECT", "")
    token = os.environ.get("TEMPO_DEVELOPMENT_BUDGET_TOKEN", "")
    if (os.environ.get("TEMPO_TEST_INSTANCE") != "disposable" or not token
            or not re.fullmatch(r"tempo-pg-regressions-[0-9]+-[a-f0-9]+", project)):
        pytest.fail("An explicit existing disposable product project and its budget owner are required")
    containers = {}
    for service in ("api", "foreground-worker", "postgres"):
        ids = _docker("ps", "-q", "--no-trunc", "--filter", "label=com.docker.compose.project=" + project,
                      "--filter", "label=com.docker.compose.service=" + service).split()
        assert len(ids) == 1, "Exactly one running owned service is required: " + service
        info, = json.loads(_docker("inspect", ids[0]))
        labels = info["Config"]["Labels"]
        assert labels["com.docker.compose.project"] == project
        assert labels["com.docker.compose.service"] == service
        assert labels["tempo.resource-class"] == "development"
        assert labels["tempo.resource-owner"] == token
        assert info["State"]["Running"] is True
        containers[service] = info
    for service in ("api", "foreground-worker"):
        container = containers[service]
        assert re.fullmatch(r"sha256:[a-f0-9]{64}", container["Image"]), "Immutable image ID required"
        image, = json.loads(_docker("image", "inspect", container["Image"]))
        assert image["Id"] == container["Image"], "Packaged image identity mismatch"
        # Neither reader nor writer may execute a mounted source overlay.
        for mount in container["Mounts"]:
            destination = mount["Destination"].rstrip("/")
            assert not any(destination == root or destination.startswith(root + "/")
                           for root in ("/app", "/packages", "/contracts", "/services"))
    return containers


COMMON = r'''
import hashlib, json, os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict
from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

def connect(role):
    assert os.environ.get("TEMPO_TEST_INSTANCE") == "disposable"
    key = "TEMPO_DATABASE_READ_URL" if role == "tempo_reader" else "TEMPO_DATABASE_WRITE_URL"
    dsn = os.environ[key]
    configured = conninfo_to_dict(dsn)
    assert configured["user"] == role and configured["dbname"] == "tempo"
    assert configured["host"] == "postgres"
    database = psycopg.connect(dsn, autocommit=True, connect_timeout=2)
    try:
        row = database.execute("""SELECT current_database(),current_schema(),current_user,
            host(inet_server_addr()),shobj_description(oid,'pg_database')
            FROM pg_database WHERE datname=current_database()""").fetchone()
        assert row[:3] == ("tempo", "public", role)
        assert row[3] in payload["postgres_addresses"], ("Database address differs from owned fixture", row[3], payload["postgres_addresses"])
        assert row[4] == "tempo-disposable-postgres-test"
        versions = database.execute("SELECT version FROM tempo_schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in versions] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
        return database
    except BaseException:
        database.close()
        raise
'''

PREFLIGHT = r'''
assert not os.environ.get("TEMPO_DATABASE_WRITE_URL")
for name, expected in payload["runtime_hashes"].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
with connect("tempo_reader") as database:
    role = database.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
    assert role == (False, False, False, False)
    assert database.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    relations = database.execute("""SELECT tablename FROM pg_tables WHERE schemaname='public'
        AND tablename IN ('imported_games','repertoire_lines','repertoires') ORDER BY tablename""").fetchall()
    assert [row[0] for row in relations] == ['imported_games','repertoire_lines','repertoires']
    for relation, in relations:
        privileges = database.execute("""SELECT has_table_privilege(current_user,%s,'SELECT'),
            has_table_privilege(current_user,%s,'INSERT'),has_table_privilege(current_user,%s,'UPDATE'),
            has_table_privilege(current_user,%s,'DELETE'),has_table_privilege(current_user,%s,'TRUNCATE')""",
            (relation,)*5).fetchone()
        assert privileges == (True, False, False, False, False), relation
    # Test the ACL, rather than relying only on the default read-only setting.
    try:
        with database.transaction():
            database.execute("SET TRANSACTION READ WRITE")
            database.execute("UPDATE imported_games SET id=id WHERE false")
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise AssertionError("Reader UPDATE must be rejected even in a read-write transaction")
print(json.dumps({"schema":POSTGRES_SCHEMA_VERSION,"role":"tempo_reader","select_grants":len(relations),"update_rejected":True}))
'''

SEED = r'''
from chess import STARTING_FEN
with connect("tempo_writer") as database:
    with database.transaction():
        database.execute("SET LOCAL statement_timeout='250ms'")
        database.execute("SET LOCAL lock_timeout='100ms'")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,'2026-01-01')",
                         (payload["identity"],payload["identity"],payload["identity"]))
        database.execute("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
            VALUES(%s,%s,%s,'white',%s,%s,'2026-01-01')""",
            (payload["identity"],payload["identity"],payload["identity"],STARTING_FEN,json.dumps(['e2e4','e7e5','g1f3','b8c6'])))
        database.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
            VALUES(%s,'fixture',%s,'2099-01-01','rapid',0,'white','1-0',%s,%s)""",
            (payload["identity"],payload["identity"],STARTING_FEN,json.dumps(['e2e4','e7e5','f1c4'])))
print(json.dumps({"seeded":payload["identity"]}))
'''

PROOF = r'''
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import quote

def request(path,body=None):
    data = None if body is None else json.dumps(body).encode()
    query = Request("http://127.0.0.1:8000"+path,data=data,headers={"Content-Type":"application/json"})
    try:
        with urlopen(query,timeout=5) as response:
            return response.status,json.load(response)
    except HTTPError as response:
        return response.code,json.load(response)

with connect("tempo_reader") as database:
    def snapshot():
        tables = ('cards','daily_queue','queue_attempt_origins','reviews','review_attempt_receipts',
                  'study_position_issued_plans','study_position_grades','study_position_states',
                  'imported_games','repertoires','repertoire_lines')
        return {table:database.execute("SELECT COALESCE(json_agg(t ORDER BY t::text),'[]'::json)::text FROM (SELECT * FROM "+table+") t").fetchone()[0]
                for table in tables}
    before = snapshot()
    status,health = request("/api/health")
    assert status == 200 and health["test_instance"] is True
    status,listing = request("/api/product/game-review/games")
    assert status == 200 and any(game['id'] == payload['identity'] for game in listing['games'])
    status,saved = request("/api/product/game-review/games/"+quote(payload['identity'],safe='')+"?repertoire_id="+quote(payload['identity'],safe=''))
    assert status == 200, saved
    status,pasted = request("/api/product/game-review/compare", {
        "pgn": '[Event "Owned reader fixture"]\n\n1. e4 e5 2. Bc4 *',
        "repertoire_id":payload['identity'],"color":"white"})
    assert status == 200, pasted
    for detail in (saved,pasted):
        assert detail['moves'] == ['e2e4','e7e5','f1c4'] and len(detail['positions']) == 4
        comparison = detail['comparison']
        assert comparison['kind'] == 'departure' and comparison['ply'] == 3
        assert comparison['played_uci'] == 'f1c4' and comparison['expected_uci'] == ['g1f3']
        assert comparison['actor'] == 'white'
        assert 'receipts' not in detail  # retired feedback contract is not restored
    assert saved['comparison'] == pasted['comparison'] and saved['positions'] == pasted['positions']
    try:
        with database.transaction():
            database.execute("UPDATE imported_games SET result='*' WHERE id=%s", (payload['identity'],))
    except (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege):
        pass
    else:
        raise AssertionError("Reader unexpectedly updated fixture game")
    assert snapshot() == before
print(json.dumps({"saved_and_pasted":True,"reader_update_rejected":True,"study_and_fixture_rows_unchanged":True}))
'''

CLEANUP = r'''
with connect("tempo_writer") as database:
    with database.transaction():
        database.execute("SET LOCAL statement_timeout='250ms'")
        database.execute("SET LOCAL lock_timeout='100ms'")
        # Independent exact predicates also clean up a lost seed acknowledgment.
        database.execute("DELETE FROM imported_games WHERE id=%s AND username=%s AND provider='fixture'",
                         (payload['identity'],payload['identity']))
        database.execute("DELETE FROM repertoire_lines WHERE id=%s AND repertoire_id=%s AND name=%s",
                         (payload['identity'],)*3)
        database.execute("DELETE FROM repertoires WHERE id=%s AND source_name=%s AND name=%s",
                         (payload['identity'],)*3)
        for table in ('imported_games','repertoire_lines','repertoires'):
            assert database.execute("SELECT COUNT(*) FROM "+table+" WHERE id=%s",(payload['identity'],)).fetchone()[0] == 0
print(json.dumps({"fixture_cleanup_verified":True}))
'''


def _exec_python(container, payload, body):
    source = COMMON + "\npayload=json.loads(" + repr(json.dumps(payload)) + ")\n" + body
    instance = "TEMPO_TEST_INSTANCE=" + os.environ["TEMPO_TEST_INSTANCE"]
    return json.loads(_docker("exec", "-i", "--env", instance, container["Id"], "python", "-",
                              source=source).strip().splitlines()[-1])


def check_packaged_game_review_reader_without_study_writes():
    containers = _owned_containers()
    addresses = [network["IPAddress"] for network in containers["postgres"]["NetworkSettings"]["Networks"].values()]
    assert addresses and all(addresses)
    payload = dict(identity="game-review-reader-"+uuid.uuid4().hex,postgres_addresses=addresses,
        runtime_hashes={target:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path,target in RUNTIME_FILES.items()})
    preflight = _exec_python(containers["api"],payload,PREFLIGHT)
    assert preflight == dict(schema=POSTGRES_SCHEMA_VERSION,role="tempo_reader",select_grants=3,update_rejected=True)
    print(json.dumps({"packaged_images": {service: containers[service]["Image"] for service in ("api", "foreground-worker")},
                      "runtime_hashes": payload["runtime_hashes"]}))
    try:
        assert _exec_python(containers["foreground-worker"],payload,SEED) == {"seeded":payload["identity"]}
        proof = _exec_python(containers["api"],payload,PROOF)
        assert proof == dict(saved_and_pasted=True,reader_update_rejected=True,study_and_fixture_rows_unchanged=True)
    finally:
        assert _exec_python(containers["foreground-worker"],payload,CLEANUP) == {"fixture_cleanup_verified":True}


if __name__ == "__main__":
    check_packaged_game_review_reader_without_study_writes()
    print("PASS packaged saved/pasted Game Review with no Study writes; task fixture cleanup verified")
