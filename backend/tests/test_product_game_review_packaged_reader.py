"""One packaged reader proof against an ALREADY owned disposable product stack.

Run with the existing validation lock and explicit TEMPO_TEST_INSTANCE=disposable,
TEMPO_TEST_COMPOSE_PROJECT=tempo-pg-regressions-<digits>-<hex>, and that stack's
TEMPO_DEVELOPMENT_BUDGET_TOKEN. API, foreground-worker and PostgreSQL must already
be running with the authoritative schema and provisioned reader/writer profiles. Nothing here
creates a container, credential, role, grant, image, or application instance.
Only three uniquely named synthetic receipt rows are seeded/removed by the
existing writer. Collection performs no Docker, HTTP, database or fixture work.

The separately selected settlement case consumes the existing Games fixture's
one acknowledged command. It publishes the registered Study sweep and saves
ordinary reviews through the real reader API/command worker, without seeding
or directly mutating Study state. Its owning controller supplies and cleans the
already verified disposable stack and exact synthetic manifest.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import pytest
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = {
    "backend/app/main.py": "/app/app/main.py",
    "backend/app/product_composition/game_review.py": "/app/app/product_composition/game_review.py",
    "backend/app/games/feedback/http.py": "/app/app/games/feedback/http.py",
    "backend/app/games/feedback/review.py": "/app/app/games/feedback/review.py",
    "backend/app/contracts/product_v02/learning.py": "/app/app/contracts/product_v02/learning.py",
    "backend/app/contracts/product_v02/presentation.py": "/app/app/contracts/product_v02/presentation.py",
    "backend/app/study/encounter_feedback.py": "/app/app/study/encounter_feedback.py",
    "database/tempo_legacy_database/adapters/games/product_review.py": "/app/tempo_legacy_database/adapters/games/product_review.py",
    "database/tempo_legacy_database/adapters/study/game_feedback.py": "/app/tempo_legacy_database/adapters/study/game_feedback.py",
    "database/tempo_legacy_database/ddl/schema_version.py": "/app/tempo_legacy_database/ddl/schema_version.py",
    "database/tempo_legacy_database/ddl/migrations/052_product_game_sync.sql": "/app/tempo_legacy_database/ddl/migrations/052_product_game_sync.sql",
    "database/tempo_legacy_database/ddl/migrations/053_study_recombination.sql": "/app/tempo_legacy_database/ddl/migrations/053_study_recombination.sql",
    "database/tempo_legacy_database/ddl/migrations/054_product_repertoire_review.sql": "/app/tempo_legacy_database/ddl/migrations/054_product_repertoire_review.sql",
}


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
    api = containers["api"]
    # A source overlay is not evidence about the packaged application.
    for mount in api["Mounts"]:
        destination = mount["Destination"].rstrip("/")
        assert not any(destination == root or destination.startswith(root + "/")
                       for root in ("/app", "/packages", "/contracts", "/services"))
    return containers


COMMON = r'''
import hashlib, json, os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict

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
        assert [row[0] for row in versions] == list(range(1,payload["schema_version"]+1))
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
        AND (tablename LIKE 'product_feedback_%' OR tablename='study_game_feedback') ORDER BY tablename""").fetchall()
    assert [row[0] for row in relations] == sorted([
        'product_feedback_jobs','product_feedback_pages','product_feedback_sources',
        'product_feedback_captures','product_feedback_deliveries','product_feedback_configuration',
        'product_feedback_sync_requests','product_feedback_retry_requests','study_game_feedback'])
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
            database.execute("UPDATE product_feedback_sources SET version=version WHERE false")
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise AssertionError("Reader UPDATE must be rejected even in a read-write transaction")
print(json.dumps({"schema":payload["schema_version"],"role":"tempo_reader","select_grants":9,"update_rejected":True}))
'''

SEED = r'''
from datetime import datetime
from app.study.interfaces.game_priority import GameEncounter,GameEffectControl
from app.study.game_feedback import Effect,effect_id
from app.study.encounter_feedback import encounter_digest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
command = GameEncounter.model_validate_json(json.dumps(payload["command"]))
now = datetime.fromisoformat(command.detected_at.replace("Z","+00:00"))
effect = Effect(key=command.key,source_revision=command.source.source_revision,received_at=now,
    status="held",encounter=command,payload_hash=encounter_digest(command),
    reason="reanalysis",disposition="first_introduction",admission_status="pending",
    deferred_reason="authority_unavailable",observation_recorded=True,
    control=GameEffectControl(key=command.key,source_revision=command.source.source_revision,
                              action="hold",reason="reanalysis"))
with connect("tempo_writer") as database:
    with database.transaction():
        database.execute("SET LOCAL transaction_timeout='250ms'")
        database.execute("SET LOCAL statement_timeout='250ms'")
        database.execute("SET LOCAL lock_timeout='100ms'")
        database.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s::jsonb)",
                         (payload["source_key"],json.dumps(payload["source"])))
        database.execute("""INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,
            command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'encounter','acknowledged',0,%s::jsonb)""",
            (payload["operation_id"],payload["source_key"],command.key.logical_id(),json.dumps(payload["delivery"])))
        PostgresGameFeedbackStore(PostgresConnection(database)).save(effect,now)
print(json.dumps({"effect_id":effect_id(command.key)}))
'''

PROOF = r'''
from urllib.request import Request,urlopen
from urllib.error import HTTPError
def request(path,body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request("http://127.0.0.1:8000"+path,data=data,headers={"Content-Type":"application/json"})
    try:
        with urlopen(request,timeout=5) as response:
            return response.status,json.load(response)
    except HTTPError as response:
        return response.code,json.load(response)
with connect("tempo_reader") as database:
    def snapshot():
        return [database.execute(sql,(value,)).fetchone()[0] for sql,value in (
            ("SELECT payload::text FROM product_feedback_sources WHERE source_key=%s",payload["source_key"]),
            ("SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s",payload["operation_id"]),
            ("SELECT row_to_json(f)::text FROM study_game_feedback f WHERE effect_key=%s",payload["effect_id"]))]
    before = snapshot()
    status,health = request("/api/health")
    assert status == 200 and health["test_instance"] is True
    status,detail = request("/api/product/game-review/games/lichess/"+payload["identity"])
    assert status == 200, detail
    receipt, = detail["receipts"]
    assert receipt["owner_receipt_id"] == payload["effect_id"]
    assert receipt["key"] == payload["command"]["key"]
    assert receipt["status"] == "held" and receipt["disposition"] == "first_introduction"
    assert receipt["admission_status"] == "pending" and receipt["available_by"] is None
    assert receipt["deferred_reason"] == "authority_unavailable" and receipt["observation_recorded"] is True
    assert receipt["source_digest"] == payload["command"]["source"]["source_digest"]
    status,rejected = request("/api/product/game-review/unregistered-write",{"operation_id":payload["identity"]})
    # Only the three adopted commands dispatch through the durable gateway.
    # The cutover guard still refuses arbitrary write paths at the API reader.
    assert status == 503 and rejected == {"detail":
        "This write route is awaiting its Celery cutover; use the current local Docker service."}
    assert snapshot() == before
print(json.dumps({"http_receipt":receipt,"writer_command_rejected":True,"fixture_rows_unchanged":True}))
'''

CLEANUP = r'''
with connect("tempo_writer") as database:
    with database.transaction():
        database.execute("SET LOCAL statement_timeout='250ms'")
        # Exact command/source predicates preserve every other fixture's rows,
        # including if seed acknowledgement was lost after its commit.
        database.execute("""DELETE FROM study_game_feedback WHERE effect_key=%s
            AND identity_json::jsonb->>'game_id'=%s""",(payload["effect_id"],payload["identity"]))
        database.execute("""DELETE FROM product_feedback_deliveries WHERE operation_id=%s AND source_key=%s
            AND payload->'encounter'->'key'->>'game_id'=%s""",
            (payload["operation_id"],payload["source_key"],payload["identity"]))
        database.execute("""DELETE FROM product_feedback_sources WHERE source_key=%s
            AND payload->'normalized'->>'game_id'=%s""",(payload["source_key"],payload["identity"]))
        for table,column,value in (("study_game_feedback","effect_key",payload["effect_id"]),
                ("product_feedback_deliveries","operation_id",payload["operation_id"]),
                ("product_feedback_sources","source_key",payload["source_key"])):
            assert database.execute("SELECT COUNT(*) FROM "+table+" WHERE "+column+"=%s",(value,)).fetchone()[0] == 0
print(json.dumps({"fixture_cleanup_verified":True}))
'''


def _exec_python(container, payload, body):
    source = COMMON + "\npayload=json.loads(" + repr(json.dumps(payload)) + ")\n" + body
    return json.loads(_docker("exec", "-i", container["Id"], "python", "-", source=source).strip().splitlines()[-1])


def test_packaged_game_review_reader_reads_current_receipt_without_write_privileges():
    containers = _owned_containers()  # Reject ambient/live targets before any connection or seed.
    from app.games.feedback.coordinator import delivery_for
    from app.games.feedback.ports import SourceState
    from app.study.game_feedback import effect_id
    from test_product_game_review_receipts import scenario

    case = scenario()
    identity = "game-review-reader-" + uuid.uuid4().hex
    replacements = {case.source.normalized.workspace_id:"legacy",case.source.normalized.game_id:identity,
        case.source.original.source_game_id:identity,case.source.source_key:identity+"-source",
        case.command.key.decision_id:identity+"-decision",case.command.decision.presentation_id:identity+"-card"}
    def namespace(value):
        if isinstance(value,dict): return {key:namespace(item) for key,item in value.items()}
        if isinstance(value,list): return [namespace(item) for item in value]
        return replacements.get(value,value) if isinstance(value,str) else value
    source = SourceState.model_validate_json(json.dumps(namespace(case.source.model_dump(mode="json"))))
    source = source.model_copy(update={"version":1})
    command, = source.encounters
    cached = type(case.delivery.receipt).model_validate_json(json.dumps(namespace(case.delivery.receipt.model_dump(mode="json"))))
    assert cached.admission_status == "available"  # The API must read past this stale producer receipt.
    delivery = delivery_for(source.source_key,command).model_copy(update={"state":"acknowledged","receipt":cached})
    addresses = [network["IPAddress"] for network in containers["postgres"]["NetworkSettings"]["Networks"].values()]
    assert addresses and all(addresses)
    payload = dict(identity=identity,source_key=source.source_key,operation_id=delivery.operation_id,
        schema_version=POSTGRES_SCHEMA_VERSION,
        source=source.model_dump(mode="json"),delivery=delivery.model_dump(mode="json"),
        command=command.model_dump(mode="json"),effect_id=effect_id(command.key),postgres_addresses=addresses,
        runtime_hashes={target:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path,target in RUNTIME_FILES.items()})
    preflight = _exec_python(containers["api"],payload,PREFLIGHT)
    assert preflight == dict(schema=POSTGRES_SCHEMA_VERSION,role="tempo_reader",select_grants=9,update_rejected=True)
    try:
        assert _exec_python(containers["foreground-worker"],payload,SEED)["effect_id"] == payload["effect_id"]
        proof = _exec_python(containers["api"],payload,PROOF)
        assert proof["writer_command_rejected"] and proof["fixture_rows_unchanged"]
    finally:
        assert _exec_python(containers["foreground-worker"],payload,CLEANUP) == {"fixture_cleanup_verified":True}


def test_packaged_provider_acknowledgment_reaches_independent_study_and_settles():
    """One actual receipt: CLI acknowledgment -> real sweep -> HTTP save/reload.

    Requires the existing controller's explicit one-game manifest and packaged
    image. No startup, credentials, clock substitution or queue write is here.
    """
    import time
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from app.contracts.product_v02.presentation import GameDetail

    containers = _owned_containers()
    manifest_path = Path(os.environ.get("TEMPO_GAME_SYNC_FIXTURE_MANIFEST", "")).resolve()
    assert manifest_path.parent.parent == ROOT.resolve()
    assert manifest_path.parent.name.startswith(".tempo-pg-test-secrets-491-reader-")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["project"] == os.environ["TEMPO_TEST_COMPOSE_PROJECT"]
    assert re.fullmatch(r"[a-f0-9]{32}", manifest["token"])
    assert manifest["repertoire_id"] == "fixture-t10-primary-repertoire-" + manifest["token"]
    assert manifest["expected_misses"] == 1
    image = os.environ.get("TEMPO_GAME_SYNC_IMAGE_ID", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    for service in ("api", "foreground-worker"):
        assert containers[service]["Image"] == image
    assert "--queues=foreground" in containers["foreground-worker"]["Config"]["Cmd"]
    bindings = containers["api"]["NetworkSettings"]["Ports"]["8000/tcp"]
    assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
    base = "http://127.0.0.1:" + bindings[0]["HostPort"]
    preflight = _exec_python(containers["api"], dict(runtime_hashes={
        target: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
        for path, target in {**RUNTIME_FILES,
            "backend/app/product_composition/provider_priority.py": "/app/app/product_composition/provider_priority.py",
            "backend/app/product_composition/feedback_maintenance.py": "/app/app/product_composition/feedback_maintenance.py",
            "backend/app/product_composition/game_sync_current.py": "/app/app/product_composition/game_sync_current.py",
            "backend/app/tasks.py": "/app/app/tasks.py",
        }.items()}), """
assert not os.environ.get('TEMPO_DATABASE_WRITE_URL')
for path, digest in payload['runtime_hashes'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
print(json.dumps({'reader_has_no_writer':True,'packaged_hashes_match':True}))
""")
    assert preflight == dict(reader_has_no_writer=True, packaged_hashes_match=True)

    def helper(action):
        output = _docker("exec", "-i", "-e", "TEMPO_TEST_INSTANCE=disposable",
            containers["foreground-worker"]["Id"], "python", "-", action,
            "--project", manifest["project"], "--fixture-json", json.dumps(manifest),
            source=(ROOT/"backend/tests/fixtures/product_game_sync_browser.py").read_text())
        return json.loads(output.strip().splitlines()[-1])

    def request(path, body=None, operation=None):
        headers = {"Content-Type": "application/json"}
        if operation:
            headers["Idempotency-Key"] = operation
        query = Request(base+path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(query, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def wait_for(read, predicate):
        deadline = time.monotonic()+45
        while True:
            value = read()
            if predicate(value):
                return value
            assert time.monotonic() < deadline, value
            time.sleep(.5)

    def current_receipt():
        status, body = request("/api/product/game-review/games/lichess/" + manifest["game_id"])
        assert status == 200, body
        detail = GameDetail.model_validate_json(json.dumps(body))
        assert len(detail.receipts) == 1
        return detail.receipts[0]

    before = helper("settlement-state")
    assert before["sources"] == before["effects"] == len(before["deliveries"]) == 1
    delivery, = before["deliveries"]
    assert delivery["state"] == "acknowledged" and delivery["attempts"] == 1 and delivery["error"] is None
    cached = delivery["receipt"]
    assert cached["observation_recorded"] and cached["disposition"] == "first_introduction"
    assert cached["admission_status"] == "pending"
    assert helper("inspect")["active"] == manifest["original_active"]
    assert before["target_reviews"] == []
    original = current_receipt()
    assert original.owner_receipt_id == cached["owner_receipt_id"]
    assert original.admission_status == "pending" and original.available_by is None

    # Beat is deliberately absent from this finite fixture. Supply bounded
    # wakes through the SAME registered/coalesced task, as the existing browser
    # fixture does, so one valid transaction deferral cannot starve recovery.
    # Claim/admission/backoff, the 250ms transaction bound and assertions stay
    # unchanged; there is no direct sweep or queue mutation in the helper.
    next_wake, wakes = 0.0, 0
    wake_deadline = time.monotonic()+45
    def refreshed_receipt():
        nonlocal next_wake, wakes
        value = current_receipt()
        now = time.monotonic()
        if value.admission_status == "pending" and next_wake <= now < wake_deadline and wakes < 23:
            wakes += 1
            helper("refresh")
            next_wake = time.monotonic()+2
        return value
    try:
        available = wait_for(refreshed_receipt, lambda value: value.admission_status == "available")
    except Exception:
        try:
            print('Study feedback task state before cleanup:', json.dumps(helper('feedback-diagnostics')))
        except Exception as diagnostic_error:
            print('Study feedback task state unavailable:', type(diagnostic_error).__name__)
        raise
    assert available.owner_receipt_id == original.owner_receipt_id
    assert available.detected_at == original.detected_at
    after_refresh = helper("settlement-state")
    assert after_refresh["unrelated"] == before["unrelated"]
    assert after_refresh["target_reviews"] == []  # First introduction is no fabricated review.
    assert helper("inspect")["active"] == manifest["original_active"]

    def head(expected):
        status, body = wait_for(lambda: request("/api/queue/today"),
            lambda value: value[0] == 200 and bool(value[1].get("cards")))
        assert status == 200 and body["cards"][0]["id"] == expected, body
        return body["cards"][0]

    def save(card, name):
        attempt = "settlement-" + name + "-" + manifest["token"]
        body = dict(outcome="correct", guided=False, queue_entry_id=card["queue_entry_id"],
                    expected_revision=card["revision"], attempt_id=attempt)
        path = "/api/cards/" + card["id"] + "/review"
        status, result = request(path, body, attempt)
        if status == 202:
            assert result["operation_id"] == attempt
            _, receipt = wait_for(lambda: request("/api/operations/"+attempt),
                lambda value: value[0] == 200 and value[1]["state"] in {"complete", "failed"})
            assert receipt["state"] == "complete", receipt
            result = receipt["response"]
        else:
            assert status == 200, result
        assert result["persisted"] is True
        repeated_status, repeated = request(path, body, attempt)
        assert repeated_status == 200 and repeated["review_id"] == result["review_id"]
        return result

    save(head(manifest["unrelated_card"]), "protected")
    protected_after_save = helper("settlement-state")["unrelated"]
    saved = save(head(manifest["card_id"]), "target")
    settled = wait_for(current_receipt, lambda value: value.admission_status == "settled")
    assert settled.owner_receipt_id == original.owner_receipt_id and settled.detected_at == original.detected_at
    assert current_receipt() == settled  # New reader request after normal saved review/reload.
    final = helper("settlement-state")
    assert final["sources"] == final["effects"] == len(final["deliveries"]) == 1
    assert final["unrelated"] == protected_after_save
    assert [row[0] for row in final["target_reviews"]] == [saved["review_id"]]
    assert all(row[1] != "game_lapse" for row in final["target_reviews"])
