"""Native ownership, durability and deterministic interleaving proofs.

Only the owning runner's fresh disposable cluster is accepted. No test discovers
or connects to live Study infrastructure.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import subprocess
import sys
from threading import Barrier, Event
from pathlib import Path
import time
import uuid

import chess
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from tempo_content.migrate import migrate
from tempo_content.domain import build_publication, canonical_position, stable_id
from tempo_content.models import SourceSnapshot
from tempo_content.store import ContentService
from tempo_content_publication_contracts.v1 import Publication, serialize_contract
from tempo_content.bridge import Bridge, digest


def source_fixture():
    source_key = "native-" + uuid.uuid4().hex
    # Distinct exact route artifacts keep orphan tests independent, while their
    # canonical legal positions still exercise global position/reply reuse.
    route_fen = chess.STARTING_FEN.rsplit(" ", 1)[0] + " " + str(int(source_key[-8:], 16) + 1)
    return {
        "schema_version": 1,
        "workspace_id": os.environ["TEMPO_CONTENT_WORKSPACE"],
        "source_key": source_key,
        "title": "Native authored source",
        "repertoires": [{"key": "white", "title": "White", "learner_color": "white"},
                        {"key": "white-shared", "title": "Shared", "learner_color": "white"}],
        "policies": [{"key": "main", "repertoire_key": "white"},
                     {"key": "shared", "repertoire_key": "white-shared"}],
        "routes": [{"key": "e4", "starting_fen": route_fen,
                    "moves": ["e2e4", "e7e5", "g1f3"]}],
        "memberships": [{"repertoire_key": "white", "policy_key": "main", "route_key": "e4"},
                        {"repertoire_key": "white-shared", "policy_key": "shared", "route_key": "e4"}],
        "exercises": [{"key": "e4-card", "decision_key": "root", "policy_key": "main",
                       "starting_fen": chess.STARTING_FEN, "prompt": "Play e4",
                       "specification": {"type": "move_line", "accepted_lines": [["e2e4"]],
                                         "mode": "single", "grading_policy": "reference"}},
                      {"key": "shared-card", "decision_key": "root", "policy_key": "shared",
                       "starting_fen": chess.STARTING_FEN, "prompt": "Play your shared move",
                       "specification": {"type": "move_line", "accepted_lines": [["e2e4"]],
                                         "mode": "single", "grading_policy": "reference"}}],
        "bindings": [{"route_key": "e4", "policy_key": "main", "ply": 0, "exercise_key": "e4-card"},
                     {"route_key": "e4", "policy_key": "shared", "ply": 0, "exercise_key": "shared-card"}],
    }


@pytest.fixture
def service():
    name = os.environ.get("TEMPO_CONTENT_TEST_CONTAINER", "")
    if not name.startswith("tempo-content-"):
        raise RuntimeError("Run the Content-owned disposable PostgreSQL runner")
    instance = ContentService(os.environ["TEMPO_CONTENT_DATABASE_URL"], os.environ["TEMPO_CONTENT_WORKSPACE"], pool_size=2)
    try:
        yield instance
    finally:
        instance.close()


def reopen(application_name=None):
    connection = os.environ["TEMPO_CONTENT_DATABASE_URL"]
    if application_name:
        connection = make_conninfo(connection, application_name=application_name)
    return ContentService(connection, os.environ["TEMPO_CONTENT_WORKSPACE"], pool_size=2)


def query(statement, parameters=()):
    with psycopg.connect(os.environ["TEMPO_CONTENT_DATABASE_URL"]) as database:
        return database.execute(statement, parameters).fetchall()


def finish(service, source_key, maximum=10000):
    for _ in range(maximum):
        status = service.inventory_status(source_key)
        if status["state"] in {"complete", "retirement_required"}:
            return status
        assert status["state"] != "failed", status
        service.work_once(source_key)
    raise AssertionError("Bounded reconstruction failed to finish within the declared fixture budget")


def publish(service, source):
    loaded = service.load_source(source, expected_revision=0)
    finish(service, source["source_key"])
    return loaded, service.publication_build(source["source_key"])


def bridge_draft(service):
    import hashlib
    source = source_fixture()
    manifest = {"schema_version":1,"kind":"authored_import","workspace_id":service.workspace_id,
        "source_key":source["source_key"],"title":source["title"],"complete":True,
        "provenance":{"kind":"synthetic","source_sha256":hashlib.sha256(json.dumps(source).encode()).hexdigest(),"description":"Native A fixture"}}
    return Bridge(service).draft(source,manifest,0)


def test_A_approval_not_persisted_cannot_authorize_apply(service):
    draft = bridge_draft(service)
    with pytest.raises(ValueError,match="No durable approved"):
        Bridge(service).prepare(draft['id'])
    assert query('SELECT count(*) FROM content.sources WHERE id=%s',(draft['source_id'],))[0][0]==0


def test_A_restart_after_approval_recovers_same_operation(service):
    bridge = Bridge(service)
    draft = bridge_draft(service)
    operation = bridge.approve(draft['id'],draft['digest'])
    with reopen() as restarted:
        repeated = Bridge(restarted).approve(draft['id'],draft['digest'])
        assert repeated['id']==operation['id']
        prepared = Bridge(restarted).prepare(operation['id'],100)
        assert prepared['document']['export']['publication']['definitions']
        assert Bridge(restarted).prepare(operation['id'],100)['document']==prepared['document']


@pytest.mark.parametrize('first',['cancel','apply'])
def test_A_cancel_activation_race_authoritative_orders(service,first):
    bridge = Bridge(service)
    draft = bridge_draft(service)
    operation = bridge.approve(draft['id'],draft['digest'])
    if first=='cancel':
        assert bridge.cancel(operation['id'])['cancelled']
        with reopen() as restarted,pytest.raises(ValueError,match='Cancelled'):
            Bridge(restarted).prepare(operation['id'])
        assert query('SELECT count(*) FROM content.sources WHERE id=%s',(draft['source_id'],))[0][0]==0
    else:
        bridge.prepare(operation['id'],100)
        assert bridge.cancel(operation['id'])=={'operation_id':operation['id'],'cancelled':False,'reason':'application_started'}


def test_A_competing_approval_reserves_source_and_digest(service):
    bridge=Bridge(service)
    draft=bridge_draft(service)
    with pytest.raises(ValueError,match='exact inspected'):
        bridge.approve(draft['id'],'0'*64)
    approved=bridge.approve(draft['id'],draft['digest'])
    source=deepcopy(draft['document']['source'])
    source['title']='Conflicting editor'
    with pytest.raises(ValueError,match='approved operation'):
        service.load_source(source,0)
    assert bridge.cancel(approved['id'])['cancelled']
    assert service.load_source(source,0)['source_revision']==1


@pytest.mark.parametrize('fault',['count','digest'])
def test_A_incomplete_stage_manifest_cannot_be_admitted(service,fault):
    source=source_fixture()
    captured={}
    def interrupt(event,context):
        if event=='after_staging':
            captured.update(context)
            raise RuntimeError('Simulated crash before source admission')
    with pytest.raises(RuntimeError,match='Simulated crash'):
        service.load_source(source,0,hook=interrupt)
    with psycopg.connect(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']) as database:
        if fault=='count':
            database.execute("UPDATE content.input_manifests SET counts=jsonb_set(counts,'{exercises}','999'::jsonb) WHERE stage_id=%s",(captured['stage_id'],))
        else:
            database.execute("UPDATE content.input_manifests SET digest=%s WHERE stage_id=%s",('0'*64,captured['stage_id']))
    with pytest.raises(ValueError,match='Immutable source manifest'):
        service.load_source(source,0)
    assert query('SELECT count(*) FROM content.sources WHERE id=%s',(captured['source_id'],))[0][0]==0


def test_A_conflicting_page_replay_keeps_accepted_bytes(service):
    source=source_fixture()
    captured={}
    def interrupt(event,context):
        if event=='after_staging':
            captured.update(context)
            raise RuntimeError('Simulated stage crash')
    with pytest.raises(RuntimeError):
        service.load_source(source,0,hook=interrupt)
    with psycopg.connect(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']) as database:
        database.execute("UPDATE content.input_items SET payload=jsonb_set(payload,'{prompt}','\"Conflicting accepted bytes\"'::jsonb) WHERE stage_id=%s AND kind='exercises' AND ordinal=0",(captured['stage_id'],))
    with pytest.raises(ValueError,match='Immutable input page'):
        service.load_source(source,0)
    assert query("SELECT payload->>'prompt' FROM content.input_items WHERE stage_id=%s AND kind='exercises' AND ordinal=0",(captured['stage_id'],))[0][0]=='Conflicting accepted bytes'
    assert query('SELECT count(*) FROM content.sources WHERE id=%s',(captured['source_id'],))[0][0]==0


@pytest.mark.parametrize('first',['cancel','apply'])
def test_A_cancel_and_apply_contend_at_durable_authorization(service,first):
    from tempo_content.bridge import lock_scope
    bridge=Bridge(service)
    draft=bridge_draft(service)
    operation=bridge.approve(draft['id'],draft['digest'])
    names={action:'a-'+action+'-'+uuid.uuid4().hex[:8] for action in ('cancel','apply')}
    def perform(action):
        with reopen(names[action]) as application:
            adapter=Bridge(application)
            return adapter.cancel(operation['id']) if action=='cancel' else adapter.prepare(operation['id'],100)
    def blocked(action):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if query("SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'",(names[action],)):
                return
            time.sleep(.005)
        raise AssertionError('Expected actual PostgreSQL scope-lock contention')
    second='apply' if first=='cancel' else 'cancel'
    with ThreadPoolExecutor(max_workers=2) as executor:
        with service._transaction() as database:
            lock_scope(database,draft['source_id'],operation['id'])
            first_future=executor.submit(perform,first)
            blocked(first)
            second_future=executor.submit(perform,second)
            blocked(second)
        if first=='cancel':
            assert first_future.result()['cancelled']
            with pytest.raises(ValueError,match='Cancelled'):
                second_future.result()
        else:
            assert first_future.result()['document']['export']
            assert second_future.result()['reason']=='application_started'


def test_A_known_validation_failure_releases_scope_without_claiming_cancellation(service):
    bridge=Bridge(service)
    original=bridge_draft(service)
    source=deepcopy(original['document']['source'])
    source['exercises'][0]['specification']['accepted_lines']=[['e2e5']]
    draft=bridge.draft(source,original['document']['manifest'],0)
    approved=bridge.approve(draft['id'],draft['digest'])
    with pytest.raises(ValueError,match='Illegal accepted move'):
        bridge.prepare(approved['id'],100)
    failed=bridge.operation(approved['id'])
    assert failed['state']=='failed' and not failed['document'].get('delivery')
    with pytest.raises(ValueError,match='Failed pre-delivery'):
        bridge.prepare(approved['id'])
    corrected=bridge.draft(original['document']['source'],original['document']['manifest'],1)
    assert bridge.approve(corrected['id'],corrected['digest'])['state']=='approved'


def test_runtime_migration_and_foreign_database_access_are_denied(service):
    identity = query("SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user")[0]
    assert identity == (os.environ["TEMPO_CONTENT_RUNTIME_ROLE"], False, False, False, False)
    with pytest.raises(psycopg.Error):
        with psycopg.connect(os.environ["TEMPO_CONTENT_DATABASE_URL"]) as database:
            database.execute("CREATE TABLE public.content_runtime_forbidden(id integer)")
    for statement in ("CREATE TEMP TABLE content_runtime_forbidden(id integer)", "SET ROLE content_owner",
                      "UPDATE content.publications SET digest='forbidden'"):
        with pytest.raises(psycopg.Error):
            with psycopg.connect(os.environ["TEMPO_CONTENT_DATABASE_URL"]) as database:
                database.execute(statement)
    with pytest.raises(Exception):
        migrate(os.environ["TEMPO_CONTENT_DATABASE_URL"], os.environ["TEMPO_CONTENT_WORKSPACE"], os.environ["TEMPO_CONTENT_RUNTIME_ROLE"])
    foreign = json.loads(os.environ["TEMPO_CONTENT_TEST_FOREIGN_DATABASES"])
    assert set(foreign) == {"learning", "game_evidence", "intelligence"}
    for name, connection_string in foreign.items():
        with pytest.raises(psycopg.OperationalError, match="permission denied"):
            psycopg.connect(connection_string, connect_timeout=2)


def test_owner_migrations_are_explicit_idempotent_and_checksum_stable(service):
    before = query("SELECT id,checksum FROM content.migration_history ORDER BY id")
    assert before
    migrate(os.environ["TEMPO_CONTENT_MIGRATION_DATABASE_URL"], os.environ["TEMPO_CONTENT_WORKSPACE"], os.environ["TEMPO_CONTENT_RUNTIME_ROLE"])
    assert query("SELECT id,checksum FROM content.migration_history ORDER BY id") == before
    with pytest.raises(psycopg.Error):
        with psycopg.connect(os.environ["TEMPO_CONTENT_DATABASE_URL"]) as database:
            database.execute("UPDATE content.migration_history SET checksum='forbidden'")


def test_publication_source_inventory_and_identities_survive_process_restart(service):
    source = source_fixture()
    loaded, publication = publish(service, source)
    payload = serialize_contract(publication)
    with reopen() as restarted:
        assert serialize_contract(restarted.publication_show(publication.publication_id, publication.publication_generation)) == payload
        assert restarted.source_show(source["source_key"])["requested_revision"] == loaded["source_revision"]
        assert restarted.inventory_status(source["source_key"])["state"] == "complete"
        replay = restarted.load_source(source, expected_revision=0)
        assert replay["replayed"] is True
        assert replay["generation"] == loaded["generation"]
        assert serialize_contract(restarted.publication_build(source["source_key"])) == payload


def test_interrupted_reconciliation_retains_same_generation_and_cursor(service):
    source = source_fixture()
    source["routes"][0]["moves"] = ["g1f3", "g8f6", "f3g1", "f6g8"] * 20
    source["bindings"] = []
    first = service.load_source(source, expected_revision=0)
    service.work_once(source["source_key"])
    before = query("SELECT generation,phase,cursor FROM content.tasks WHERE source_id=%s ORDER BY generation", (first["source_id"],))
    with reopen() as restarted:
        replay = restarted.load_source(source, expected_revision=0)
        requested = restarted.request_inventory(source["source_key"])
        assert replay["generation"] == first["generation"]
        assert requested["generation"] == first["generation"]
        assert query("SELECT generation,phase,cursor FROM content.tasks WHERE source_id=%s ORDER BY generation", (first["source_id"],)) == before
        finish(restarted, source["source_key"])
        assert isinstance(restarted.publication_build(source["source_key"]), Publication)


def test_source_revision_changes_preserve_old_publication_and_unrelated_card_identity(service):
    source = source_fixture()
    first, old = publish(service, source)
    changed = deepcopy(source)
    changed["title"] = "Renamed source"
    changed["repertoires"].reverse()
    changed["exercises"][0]["prompt"] = "Play the authored king pawn move"
    second = service.load_source(changed, expected_revision=first["source_revision"])
    finish(service, source["source_key"])
    new = service.publication_build(source["source_key"])
    assert second["generation"] > first["generation"]
    assert new.publication_id == old.publication_id
    assert {item.card_id for item in new.definitions} == {item.card_id for item in old.definitions}
    previous = {item.card_id: item for item in old.definitions}
    for item in new.definitions:
        assert item.decision_id == previous[item.card_id].decision_id
        assert item.policy_id == previous[item.card_id].policy_id
        assert item.exercise_revision == previous[item.card_id].exercise_revision + (item.prompt != previous[item.card_id].prompt)
    assert serialize_contract(service.publication_show(old.publication_id, old.publication_generation)) == serialize_contract(old)


def test_failed_validation_and_incomplete_generation_preserve_last_complete_publication(service):
    source = source_fixture()
    first, old = publish(service, source)
    invalid = deepcopy(source)
    invalid["routes"][0]["moves"] = ["e2e5"]
    try:
        service.load_source(invalid, expected_revision=first["source_revision"])
        for _ in range(100):
            service.work_once(source["source_key"])
            if service.inventory_status(source["source_key"])["state"] == "failed":
                break
        with pytest.raises(Exception):
            service.publication_build(source["source_key"])
    except ValueError:
        pass
    assert serialize_contract(service.publication_show(old.publication_id, old.publication_generation)) == serialize_contract(old)


def test_stale_worker_cannot_commit_after_new_source_revision(service):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    read = Event()
    release = Event()

    def hook(event, context):
        if event == "after_read":
            read.set()
            assert release.wait(10), "controlled stale-worker barrier was not released"

    def stale_work():
        with reopen() as worker:
            return worker.work_once(source["source_key"], hook=hook)

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(stale_work)
        assert read.wait(10), "worker did not reach the post-read barrier"
        try:
            changed = deepcopy(source)
            changed["title"] = "A newer accepted source"
            newer = service.load_source(changed, expected_revision=loaded["source_revision"])
        finally:
            release.set()
        task.result(timeout=15)
    finish(service, source["source_key"])
    publication = service.publication_build(source["source_key"])
    assert publication.source_revision == newer["source_revision"]
    assert publication.publication_generation == newer["generation"]
    assert query("SELECT generation FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(newer["generation"],)]


def test_concurrent_publication_attempts_return_one_immutable_payload(service):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    finish(service, source["source_key"])
    start = Event()

    def build():
        with reopen() as contender:
            assert start.wait(10)
            return serialize_contract(contender.publication_build(source["source_key"]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(build), executor.submit(build)
        start.set()
        assert first.result(timeout=20) == second.result(timeout=20)
    assert query("SELECT count(*) FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(1,)]


def test_repertoire_deletion_preserves_shared_route_and_position_inventory(service):
    source = source_fixture()
    loaded, old = publish(service, source)
    artifact_ids = query("SELECT DISTINCT artifact_id FROM content.generation_routes WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"]))
    assert len(artifact_ids) == 1
    before = query("SELECT position_key,move_uci,resulting_position_key FROM content.legal_replies WHERE position_key IN (SELECT position_key FROM content.occurrences WHERE artifact_id=%s) ORDER BY 1,2", artifact_ids[0])
    assert before
    changed = deepcopy(source)
    changed["repertoires"] = changed["repertoires"][1:]
    changed["policies"] = changed["policies"][1:]
    changed["memberships"] = changed["memberships"][1:]
    changed["exercises"] = changed["exercises"][1:]
    changed["bindings"] = changed["bindings"][1:]
    service.load_source(changed, expected_revision=loaded["source_revision"])
    finish(service, source["source_key"])
    new = service.publication_build(source["source_key"])
    assert len(new.definitions) == 1
    assert new.definitions[0].card_id in {item.card_id for item in old.definitions}
    assert query("SELECT position_key,move_uci,resulting_position_key FROM content.legal_replies WHERE position_key IN (SELECT position_key FROM content.occurrences WHERE artifact_id=%s) ORDER BY 1,2", artifact_ids[0]) == before


def test_deletion_leaves_durable_bounded_collector_and_cleanup_resumes(service):
    source = source_fixture()
    source["routes"] = [{"key": f"orphan-{length}", "starting_fen": chess.STARTING_FEN,
                         "moves": (["g1f3", "g8f6", "f3g1", "f6g8"] * 18)[:length]} for length in range(1, 71)]
    source["memberships"] = [{"repertoire_key": "white", "policy_key": "main", "route_key": item["key"]} for item in source["routes"]]
    source["bindings"] = []
    loaded, old = publish(service, source)
    artifact_ids = [row[0] for row in query("SELECT DISTINCT artifact_id FROM content.generation_routes WHERE source_id=%s", (loaded["source_id"],))]
    changed = deepcopy(source)
    changed["routes"] = []
    changed["memberships"] = []
    service.load_source(changed, expected_revision=loaded["source_revision"])
    assert query("SELECT pending FROM content.collector WHERE id=1") == [(True,)]
    finish(service, source["source_key"])
    with reopen() as restarted:
        for _ in range(1000):
            before = query("SELECT count(*) FROM content.occurrences WHERE artifact_id=ANY(%s)", (artifact_ids,))[0][0]
            if before == 0:
                break
            restarted.work_once()
            after = query("SELECT count(*) FROM content.occurrences WHERE artifact_id=ANY(%s)", (artifact_ids,))[0][0]
            assert 0 <= before - after <= 64
        else:
            raise AssertionError("Durable orphan collector did not finish the bounded fixture")
        assert query("SELECT count(*) FROM content.artifacts WHERE id=ANY(%s)", (artifact_ids,)) == [(0,)]
        assert serialize_contract(restarted.publication_show(old.publication_id, old.publication_generation)) == serialize_contract(old)


def test_zero_definition_replacement_requires_retirement_and_keeps_old_publication(service):
    source = source_fixture()
    loaded, old = publish(service, source)
    changed = deepcopy(source)
    changed.update(repertoires=[], policies=[], routes=[], memberships=[], exercises=[], bindings=[])
    service.load_source(changed, expected_revision=loaded["source_revision"])
    assert finish(service, source["source_key"])["state"] == "retirement_required"
    with pytest.raises(Exception):
        service.publication_build(source["source_key"])
    assert serialize_contract(service.publication_show(old.publication_id, old.publication_generation)) == serialize_contract(old)


@pytest.mark.parametrize("fen,learner_color", [
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "black"),
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "black"),
    ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "black"),
    ("4k3/8/8/8/8/8/p7/4K3 b - - 0 1", "white"),
    ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", "white"),
], ids=["castling", "en-passant", "white-promotions", "black-promotions", "terminal"])
def test_native_inventory_contains_every_legal_reply_including_special_moves(service, fen, learner_color):
    source = source_fixture()
    for repertoire in source["repertoires"]:
        repertoire["learner_color"] = learner_color
    source["routes"][0].update(starting_fen=fen, moves=[])
    source["bindings"] = []
    for exercise in source["exercises"]:
        exercise.update(starting_fen=fen, specification={"type": "explanation", "rubric": "Explain the legal position"})
    publish(service, source)
    board = chess.Board(fen)
    key = canonical_position(fen)
    replies = service.replies(key)["items"]
    expected = {}
    for move in board.legal_moves:
        following = board.copy()
        following.push(move)
        expected[move.uci()] = canonical_position(following.fen())
    assert {row["move_uci"]: row["resulting_position_key"] for row in replies} == expected
    assert query("SELECT reply_count FROM content.positions WHERE position_key=%s", (key,)) == [(len(expected),)]


@pytest.mark.parametrize("first_holder", ["inventory", "deletion"])
def test_source_and_task_lock_order_serializes_deletion_versus_inventory(service, first_holder):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    acquired, release, staged = Event(), Event(), Event()
    deletion_label = "content-delete-" + uuid.uuid4().hex
    worker_label = "content-inventory-" + uuid.uuid4().hex

    def worker_hook(event, context):
        if first_holder == "inventory" and event == "after_source_lock" and not acquired.is_set():
            acquired.set()
            assert release.wait(10), "lock-order barrier was not released"

    def deletion_hook(event, context):
        if event == "after_staging":
            staged.set()
        if first_holder == "deletion" and event == "after_source_lock":
            acquired.set()
            assert release.wait(10), "foreground lock-order barrier was not released"

    changed = deepcopy(source)
    changed["bindings"] = changed["bindings"][1:]
    changed["exercises"] = changed["exercises"][1:]
    changed["memberships"] = changed["memberships"][1:]
    changed["policies"] = changed["policies"][1:]
    changed["repertoires"] = changed["repertoires"][1:]

    def work():
        with reopen(worker_label) as worker:
            return worker.work_once(source["source_key"], hook=worker_hook)

    def delete():
        with reopen(deletion_label) as editor:
            return editor.load_source(changed, expected_revision=loaded["source_revision"], hook=deletion_hook)

    with ThreadPoolExecutor(max_workers=2) as executor:
        if first_holder == "inventory":
            worker = executor.submit(work)
            assert acquired.wait(10)
            deletion = executor.submit(delete)
            blocked_label = deletion_label
        else:
            deletion = executor.submit(delete)
            assert acquired.wait(10)
            worker = executor.submit(work)
            blocked_label = worker_label
        try:
            assert staged.wait(10)
            deadline = time.monotonic() + 3
            blocked = False
            while time.monotonic() < deadline:
                rows = query("SELECT cardinality(pg_blocking_pids(pid)) FROM pg_stat_activity WHERE application_name=%s AND state='active'", (blocked_label,))
                if any(row[0] > 0 for row in rows):
                    blocked = True
                    break
            assert blocked, "contender did not reach the held source lock"
        finally:
            release.set()
        worker.result(timeout=15)
        deleted = deletion.result(timeout=15)
    assert deleted["source_revision"] == loaded["source_revision"] + 1
    finish(service, source["source_key"])
    assert len(service.publication_build(source["source_key"]).definitions) == 1


def test_rollback_after_inventory_writes_keeps_checkpoint_and_rows_consistent(service):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    # First delivery registers the route; the second materializes its positions.
    service.work_once(source["source_key"])
    before = query("SELECT phase,cursor FROM content.tasks WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"]))
    artifact = query("SELECT artifact_id FROM content.generation_routes WHERE source_id=%s AND generation=%s LIMIT 1", (loaded["source_id"], loaded["generation"]))[0][0]
    occurrence_before = query("SELECT count(*) FROM content.occurrences WHERE artifact_id=%s", (artifact,))
    observed = []

    def fail_after_writes(event, context):
        if event == "after_writes":
            observed.append(event)
            raise ValueError("injected_after_content_batch_writes")

    result = service.work_once(source["source_key"], hook=fail_after_writes)
    assert observed, "rollback proof requires the after-writes transaction hook"
    assert result["status"] == "failed"
    assert query("SELECT phase,cursor FROM content.tasks WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"])) == before
    assert query("SELECT count(*) FROM content.occurrences WHERE artifact_id=%s", (artifact,)) == occurrence_before
    assert query("SELECT count(*) FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(0,)]


def test_immutable_publication_replay_and_conflicting_payload_do_not_overwrite(service):
    source = source_fixture()
    loaded, publication = publish(service, source)
    provenance = query("SELECT provenance FROM content.publications WHERE source_id=%s", (loaded["source_id"],))[0][0]
    service.store_publication(publication, loaded["source_id"], provenance)
    changed = publication.model_dump(mode="json")
    changed["definitions"][0]["prompt"] = "Conflicting immutable payload"
    with pytest.raises(ValueError, match="immutable"):
        service.store_publication(Publication.model_validate(changed), loaded["source_id"], provenance)
    assert serialize_contract(service.publication_show(publication.publication_id, publication.publication_generation)) == serialize_contract(publication)
    assert query("SELECT count(*) FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(1,)]


def test_orphan_collection_between_route_read_and_registration_is_fenced(service):
    source = source_fixture()
    loaded, old = publish(service, source)
    artifact = query("SELECT artifact_id FROM content.generation_routes WHERE source_id=%s LIMIT 1", (loaded["source_id"],))[0][0]
    deleted = deepcopy(source)
    deleted.update(routes=[], memberships=[], bindings=[])
    removed = service.load_source(deleted, expected_revision=loaded["source_revision"])
    finish(service, source["source_key"])
    # Remove old references while retaining the orphan itself for the barrier.
    for _ in range(1000):
        if query("SELECT count(*) FROM content.generation_routes WHERE source_id=%s", (loaded["source_id"],)) == [(0,)]:
            break
        service.collect_once()
    else:
        raise AssertionError("obsolete route references were not collected")
    service.load_source(source, expected_revision=removed["source_revision"])
    read, release = Event(), Event()

    def hook(event, context):
        if event == "after_read" and context.get("artifact") is not None:
            read.set()
            assert release.wait(10), "route re-registration barrier was not released"

    def register():
        with reopen() as worker:
            return worker.work_once(source["source_key"], hook=hook)

    with ThreadPoolExecutor(max_workers=1) as executor:
        registration = executor.submit(register)
        assert read.wait(10)
        try:
            for _ in range(1000):
                if query("SELECT count(*) FROM content.artifacts WHERE id=%s", (artifact,)) == [(0,)]:
                    break
                service.collect_once()
            else:
                raise AssertionError("controlled orphan was not collected")
        finally:
            release.set()
        result = registration.result(timeout=15)
    assert result["status"] == "artifact_advanced"
    finish(service, source["source_key"])
    rebuilt = service.publication_build(source["source_key"])
    assert {item.card_id for item in rebuilt.definitions} == {item.card_id for item in old.definitions}
    assert query("SELECT complete FROM content.artifacts WHERE id=%s", (artifact,)) == [(True,)]
    assert query("SELECT count(*) FROM content.occurrences WHERE artifact_id=%s", (artifact,)) == [(4,)]


def test_crash_after_staging_does_not_admit_source_and_identical_retry_completes(service):
    source = source_fixture()
    reached = []

    def interrupt(event, context):
        if event == "after_staging":
            reached.append(context)
            raise RuntimeError("simulated_process_loss_before_admission")

    with pytest.raises(RuntimeError, match="before_admission"):
        service.load_source(source, expected_revision=0, hook=interrupt)
    assert reached
    assert query("SELECT count(*) FROM content.sources WHERE source_key=%s", (source["source_key"],)) == [(0,)]
    staged = query("SELECT stage_id FROM content.input_manifests WHERE source_id=%s", (reached[0]["source_id"],))
    assert staged == [(reached[0]["stage_id"],)]
    with reopen() as restarted:
        loaded, publication = publish(restarted, source)
        assert loaded["source_revision"] == 1
        assert query("SELECT stage_id FROM content.input_manifests WHERE source_id=%s", (loaded["source_id"],)) == staged
        assert publication.source_revision == 1


def test_complete_source_cli_acceptance_restarts_and_preserves_both_publications(service, tmp_path):
    source = source_fixture()
    fixture = tmp_path / "authored-source.json"
    fixture.write_text(json.dumps(source))
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    environment.update(TEMPO_CONTENT_DATABASE_URL=os.environ["TEMPO_CONTENT_DATABASE_URL"],
                       TEMPO_CONTENT_WORKSPACE=os.environ["TEMPO_CONTENT_WORKSPACE"])

    def command(*arguments):
        result = subprocess.run([sys.executable, "-I", "-m", "tempo_content.cli", *arguments],
                                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert command("readiness")["status"] == "ready"
    first = command("source", "load", str(fixture), "--expected-revision", "0")
    command("inventory", "request", source["source_key"])
    deliveries = command("inventory", "work", "--source-key", source["source_key"], "--max-slices", "32")
    assert all(item["status"] != "failed" for item in deliveries["deliveries"])
    assert command("inventory", "status", source["source_key"])["state"] == "complete"
    old = Publication.model_validate(command("publication", "build", source["source_key"]))
    assert Publication.model_validate(command("publication", "show", old.publication_id,
                                            "--generation", str(old.publication_generation))) == old
    source["title"] = "A complete new source revision"
    fixture.write_text(json.dumps(source))
    second = command("source", "load", str(fixture), "--expected-revision", str(first["source_revision"]))
    command("inventory", "work", "--source-key", source["source_key"], "--max-slices", "32")
    new = Publication.model_validate(command("publication", "build", source["source_key"]))
    assert new.publication_id == old.publication_id
    assert new.publication_generation == second["generation"] > old.publication_generation
    assert {item.card_id for item in new.definitions} == {item.card_id for item in old.definitions}
    assert Publication.model_validate(command("publication", "show", old.publication_id,
                                            "--generation", str(old.publication_generation))) == old
    print(json.dumps({"source_id": first["source_id"], "publication_id": old.publication_id,
                      "generations": [old.publication_generation, new.publication_generation],
                      "proof": "installed subprocesses restart with restricted runtime credentials"}, sort_keys=True))


def test_opposite_color_sources_can_materialize_one_shared_legal_universe_concurrently(service):
    white = source_fixture()
    black = source_fixture()
    for repertoire in black["repertoires"]:
        repertoire["learner_color"] = "black"
    black["bindings"] = []
    for exercise in black["exercises"]:
        exercise["specification"] = {"type": "explanation", "rubric": "Describe Black's plan"}
    for source in (white, black):
        service.load_source(source, expected_revision=0)
        service.work_once(source["source_key"])
    computed = Barrier(2)

    def hook(event, context):
        if event == "before_commit" and context.get("artifact") is not None:
            computed.wait(timeout=10)

    def materialize(source):
        with reopen() as worker:
            return worker.work_once(source["source_key"], hook=hook)

    with ThreadPoolExecutor(max_workers=2) as executor:
        white_task = executor.submit(materialize, white)
        black_task = executor.submit(materialize, black)
        assert white_task.result(timeout=20)["status"] == "pending"
        assert black_task.result(timeout=20)["status"] == "pending"
    for source in (white, black):
        finish(service, source["source_key"])
        service.publication_build(source["source_key"])
    assert len(service.replies(canonical_position(chess.STARTING_FEN))["items"]) == 20


def test_expired_persisted_lease_is_reclaimed_by_a_fresh_process(service, tmp_path):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    script = tmp_path / "crash_after_claim.py"
    script.write_text("import os\nfrom tempo_content.store import ContentService\n"
                      "def crash(event,context):\n    if event=='after_read': os._exit(71)\n"
                      "with ContentService(os.environ['TEMPO_CONTENT_DATABASE_URL'],os.environ['TEMPO_CONTENT_WORKSPACE']) as service:\n"
                      "    service.work_once(os.environ['TEMPO_CONTENT_LEASE_SOURCE'],hook=crash)\n")
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "TEMPO_CONTENT_DATABASE_URL", "TEMPO_CONTENT_WORKSPACE"}}
    environment["TEMPO_CONTENT_LEASE_SOURCE"] = source["source_key"]
    crashed = subprocess.run([sys.executable, "-I", str(script)], env=environment, cwd=tmp_path,
                             capture_output=True, text=True, timeout=20)
    assert crashed.returncode == 71, crashed.stderr
    lease = query("SELECT generation,lease_token,lease_until>clock_timestamp() FROM content.tasks WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"]))[0]
    assert lease[0] == loaded["generation"] and lease[1] and lease[2]
    # Deterministic fixture-only clock fault: expire only this crashed delivery.
    with psycopg.connect(os.environ["TEMPO_CONTENT_MIGRATION_DATABASE_URL"]) as database:
        database.execute("UPDATE content.tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE source_id=%s AND generation=%s AND lease_token=%s", (loaded["source_id"], loaded["generation"], lease[1]))
    resumed = subprocess.run([sys.executable, "-I", "-m", "tempo_content.cli", "inventory", "work",
                              "--source-key", source["source_key"], "--max-slices", "32"],
                             env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert resumed.returncode == 0, resumed.stderr
    assert service.inventory_status(source["source_key"])["state"] == "complete"
    assert service.inventory_status(source["source_key"])["generation"] == loaded["generation"]
    assert service.publication_build(source["source_key"]).publication_generation == loaded["generation"]


def test_registration_holds_artifact_lock_collector_yields_without_lost_wakeup(service):
    source = source_fixture()
    loaded, old = publish(service, source)
    artifact = query("SELECT artifact_id FROM content.generation_routes WHERE source_id=%s LIMIT 1", (loaded["source_id"],))[0][0]
    deleted = deepcopy(source)
    deleted.update(routes=[], memberships=[], bindings=[])
    removed = service.load_source(deleted, expected_revision=loaded["source_revision"])
    finish(service, source["source_key"])
    for _ in range(1000):
        if query("SELECT count(*) FROM content.generation_routes WHERE source_id=%s", (loaded["source_id"],)) == [(0,)]:
            break
        service.collect_once()
    else:
        raise AssertionError("obsolete references were not collected")
    service.load_source(source, expected_revision=removed["source_revision"])
    locked, release = Event(), Event()

    def hook(event, context):
        if event == "after_artifact_lock" and context.get("artifact_id") == artifact:
            locked.set()
            assert release.wait(10), "registration lock barrier was not released"

    def register():
        with reopen() as worker:
            return worker.work_once(source["source_key"], hook=hook)

    with ThreadPoolExecutor(max_workers=1) as executor:
        registration = executor.submit(register)
        assert locked.wait(10)
        try:
            result = service.collect_once()
            assert result["phase"] == "collect"
            assert query("SELECT pending FROM content.collector WHERE id=1") == [(True,)]
            assert query("SELECT complete FROM content.artifacts WHERE id=%s", (artifact,)) == [(True,)]
        finally:
            release.set()
        assert registration.result(timeout=15)["status"] == "pending"
    finish(service, source["source_key"])
    publication = service.publication_build(source["source_key"])
    assert {item.card_id for item in publication.definitions} == {item.card_id for item in old.definitions}
    assert query("SELECT complete FROM content.artifacts WHERE id=%s", (artifact,)) == [(True,)]


def test_contract_valid_forged_publication_cannot_bypass_validated_source(service):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    finish(service, source["source_key"])
    publication = build_publication(SourceSnapshot.model_validate(source), loaded["source_revision"],
                                    loaded["generation"], "2026-10-16T12:00:00Z",
                                    {item["key"]: 1 for item in source["exercises"]})
    forged = publication.model_dump(mode="json")
    forged["definitions"] = forged["definitions"][:1]
    with pytest.raises(ValueError, match="validated authored source"):
        service.store_publication(Publication.model_validate(forged), loaded["source_id"], {})
    assert query("SELECT count(*) FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(0,)]
    assert len(service.publication_build(source["source_key"]).definitions) == 2


def test_route_coverage_preserves_policy_separation_and_unauthored_legal_replies(service):
    source = source_fixture()
    board = chess.Board()
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    source["exercises"].append({"key": "main-response", "decision_key": "develop-knight", "policy_key": "main",
                                "starting_fen": board.fen(), "prompt": "Develop the knight",
                                "specification": {"type": "move_line", "accepted_lines": [["g1f3"]], "mode": "single", "grading_policy": "reference"}})
    source["bindings"].append({"route_key": "e4", "policy_key": "main", "ply": 2, "exercise_key": "main-response"})
    loaded, old = publish(service, source)
    main = {item["move_uci"]: item for item in service.coverage(source["source_key"], "e4", "main", 1)["items"]}
    shared = {item["move_uci"]: item for item in service.coverage(source["source_key"], "e4", "shared", 1)["items"]}
    board = chess.Board()
    board.push_uci("e2e4")
    assert set(main) == set(shared) == {move.uci() for move in board.legal_moves}
    assert main["e7e5"]["authored"] and main["e7e5"]["covered"]
    assert shared["e7e5"]["authored"] and shared["e7e5"]["status"] == "uncovered"
    assert main["c7c5"]["legal"] and not main["c7c5"]["authored"] and main["c7c5"]["status"] == "uncovered"
    assert all("probability" not in reply for reply in main.values())
    changed = deepcopy(source)
    changed["title"] = "Inventory pending after a structural revision"
    service.load_source(changed, expected_revision=loaded["source_revision"])
    assert service.coverage(source["source_key"], "e4", "main", 1)["status"] == "unavailable"


def test_bounded_route_position_and_reply_pages_have_no_gaps_or_duplicates(service):
    source = source_fixture()
    publish(service, source)

    def pages(reader, *arguments, limit):
        items, cursor = [], None
        for _ in range(100):
            page = reader(*arguments, limit=limit, after=cursor)
            if reader != service.replies:
                status = service.inventory_status(arguments[0])
                assert page["source_revision"] == status["revision"]
                assert page["generation"] == status["generation"]
                assert page["status"] == status["status"]
            items.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return items
        raise AssertionError("Reader failed to terminate the bounded fixture")

    routes = pages(service.routes, source["source_key"], limit=1)
    assert {(item["route_key"], item["policy_key"]) for item in routes} == {("e4", "main"), ("e4", "shared")}
    assert len(routes) == 2
    positions = pages(service.positions, source["source_key"], limit=1)
    assert len(positions) == len({item["position_key"] for item in positions}) == 2
    board = chess.Board()
    board.push_uci("e2e4")
    replies = pages(service.replies, canonical_position(board.fen()), limit=5)
    assert len(replies) == len({item["move_uci"] for item in replies}) == len(list(board.legal_moves))


def test_active_source_lease_does_not_starve_unscoped_work_for_another_source(service):
    pending = query("SELECT s.source_key FROM content.sources s JOIN content.tasks t ON t.source_id=s.id AND t.revision=s.requested_revision WHERE t.state IN ('pending','working')")
    for row in pending:
        finish(service, row[0])
    sources = sorted([source_fixture(), source_fixture()],
                     key=lambda source: stable_id(source["workspace_id"], source["source_key"], "source", source["source_key"]))
    loaded = [service.load_source(source, expected_revision=0) for source in sources]
    claimed, release = Event(), Event()

    def hold_lease(event, context):
        if event == "after_read":
            claimed.set()
            assert release.wait(10), "lease-fairness barrier was not released"

    def first_source():
        with reopen() as worker:
            return worker.work_once(sources[0]["source_key"], hook=hold_lease)

    observed = []

    def observe(event, context):
        if event == "after_read":
            observed.append(context["task"]["source_id"])

    with ThreadPoolExecutor(max_workers=1) as executor:
        lease_holder = executor.submit(first_source)
        assert claimed.wait(10)
        try:
            assert service.work_once(hook=observe)["worked"]
            assert observed == [loaded[1]["source_id"]]
        finally:
            release.set()
        lease_holder.result(timeout=15)
    for source in sources:
        finish(service, source["source_key"])


def test_runtime_readiness_rejects_drifted_grants_and_owner_migration_repairs_them(service):
    owner_url = os.environ["TEMPO_CONTENT_MIGRATION_DATABASE_URL"]
    runtime_role = os.environ["TEMPO_CONTENT_RUNTIME_ROLE"]
    try:
        with psycopg.connect(owner_url) as database:
            database.execute("GRANT UPDATE ON content.source_revisions TO content_runtime")
        with pytest.raises(ValueError, match="owner privileges"):
            reopen()
    finally:
        migrate(owner_url, os.environ["TEMPO_CONTENT_WORKSPACE"], runtime_role)
    assert query("SELECT has_table_privilege(current_user,'content.source_revisions','UPDATE')") == [(False,)]
    with reopen() as repaired:
        assert repaired.workspace_id == os.environ["TEMPO_CONTENT_WORKSPACE"]


def test_forged_initial_provenance_is_rejected_and_interrupted_build_replays(service):
    source = source_fixture()
    loaded = service.load_source(source, expected_revision=0)
    finish(service, source["source_key"])
    captured = []

    def interrupt(event, context):
        if event == "before_commit" and "publication" in context:
            captured.append(context["publication"])
            raise RuntimeError("interrupted_after_reserved_build_time")

    with pytest.raises(RuntimeError, match="reserved_build_time"):
        service.publication_build(source["source_key"], hook=interrupt)
    assert len(captured) == 1
    with pytest.raises(ValueError, match="provenance differs"):
        service.store_publication(captured[0], loaded["source_id"], {"source_id": "forged"})
    assert query("SELECT count(*) FROM content.publications WHERE source_id=%s", (loaded["source_id"],)) == [(0,)]
    with reopen() as restarted:
        legitimate = restarted.publication_build(source["source_key"])
        assert serialize_contract(legitimate) == serialize_contract(captured[0])


# PR #288 repairs: these cases run in the regular Content-owned native lane.
def staging_state():
    return {table: query('SELECT count(*) FROM content.' + table)[0][0]
            for table in ('input_items', 'input_manifests', 'source_revisions', 'tasks')}


def covered_source():
    source = source_fixture()
    board = chess.Board()
    for move in ('e2e4', 'e7e5'):
        board.push_uci(move)
    source['exercises'].append({'key': 'response', 'decision_key': 'response', 'policy_key': 'main',
        'starting_fen': board.fen(), 'prompt': 'Develop',
        'specification': {'type': 'move_line', 'accepted_lines': [['g1f3']], 'mode': 'single', 'grading_policy': 'reference'}})
    source['bindings'].append({'route_key': 'e4', 'policy_key': 'main', 'ply': 2, 'exercise_key': 'response'})
    return source


def test_coverage_snapshot_survives_generation_replacement_and_collection(service):
    source = covered_source()
    loaded, publication = publish(service, source)
    artifact = query('SELECT artifact_id FROM content.generation_routes WHERE source_id=%s AND generation=%s LIMIT 1',
                     (loaded['source_id'], loaded['generation']))[0][0]
    read, release = Event(), Event()
    observed = []

    def hook(event, context):
        if event == 'after_coverage_occurrence':
            observed.append(context)
            read.set()
            assert release.wait(20), 'coverage snapshot barrier was not released'

    def reader():
        with reopen('content-288-coverage') as worker:
            return worker.coverage(source['source_key'], 'e4', 'main', 1, hook=hook)

    with ThreadPoolExecutor(max_workers=1) as executor:
        reading = executor.submit(reader)
        try:
            assert read.wait(10), 'reader did not reach the G1 occurrence'
            replacement = deepcopy(source)
            replacement['routes'][0].update(key='d4', moves=['d2d4', 'd7d5', 'c2c4'])
            for membership in replacement['memberships']:
                membership['route_key'] = 'd4'
            replacement['bindings'] = []
            second = service.load_source(replacement, expected_revision=loaded['source_revision'])
            unavailable = service.coverage(source['source_key'], 'd4', 'main', 1)
            assert unavailable['status'] == 'unavailable'
            finish(service, source['source_key'])
            service.publication_build(source['source_key'])
            for _ in range(1000):
                service.collect_once()
                if not query('SELECT 1 FROM content.artifacts WHERE id=%s', (artifact,)):
                    break
            else:
                raise AssertionError('G1 artifact was not collected')
            assert not query('SELECT 1 FROM content.generation_routes WHERE source_id=%s AND generation=%s',
                             (loaded['source_id'], loaded['generation']))
            assert not query('SELECT 1 FROM content.occurrences WHERE artifact_id=%s', (artifact,))
        finally:
            release.set()
        result = reading.result(timeout=10)
    assert observed[0]['generation'] == loaded['generation']
    authored = next(reply for reply in result['items'] if reply['move_uci'] == 'e7e5')
    assert authored['covered'] and authored['status'] == 'covered'
    assert result['status'] == 'complete'
    assert (result['source_revision'], result['generation']) == (loaded['source_revision'], loaded['generation'])
    assert (unavailable['source_revision'], unavailable['generation']) == (second['source_revision'], second['generation'])
    fresh = service.coverage(source['source_key'], 'd4', 'main', 1)
    assert fresh['source_revision'] == second['source_revision'] and fresh['generation'] == second['generation']
    assert serialize_contract(service.publication_show(publication.publication_id, publication.publication_generation)) == serialize_contract(publication)


@pytest.mark.parametrize('reader_name', ['positions', 'routes'])
def test_inventory_cursor_rejects_changed_source_generation(service, reader_name):
    source = source_fixture()
    first, _ = publish(service, source)
    reader = getattr(service, reader_name)
    page = reader(source['source_key'], limit=1)
    assert page['next_cursor'] is not None
    replacement = deepcopy(source)
    replacement['routes'][0].update(key='d4', moves=['d2d4', 'd7d5', 'c2c4'])
    for membership in replacement['memberships']:
        membership['route_key'] = 'd4'
    replacement['bindings'] = []
    second = service.load_source(replacement, expected_revision=first['source_revision'])
    finish(service, source['source_key'])
    with pytest.raises(ValueError) as rejected:
        reader(source['source_key'], limit=1, after=page['next_cursor'])
    assert rejected.value.code == 'stale_inventory_cursor'
    for limit in (1, 2, 3):
        items, cursor = [], None
        for _ in range(20):
            current = reader(source['source_key'], limit=limit, after=cursor)
            assert (current['source_revision'], current['generation']) == (second['source_revision'], second['generation'])
            assert current['status'] == 'complete'
            items.extend(current['items'])
            cursor = current['next_cursor']
            if cursor is None:
                break
        else:
            raise AssertionError('pagination did not terminate')
        keys = ([row['position_key'] for row in items] if reader_name == 'positions'
                else [(row['route_key'], row['policy_key']) for row in items])
        expected = reader(source['source_key'], limit=256)['items']
        assert items == expected and keys == sorted(set(keys))
        assert len(keys) == 2


@pytest.mark.parametrize('reader_name', ['positions', 'routes'])
def test_inventory_cursor_rejects_malformed_and_cross_source_tokens(service, reader_name):
    source = source_fixture()
    publish(service, source)
    other = source_fixture()
    publish(service, other)
    reader = getattr(service, reader_name)
    token = reader(source['source_key'], limit=1)['next_cursor']
    wrong_kind = getattr(service, 'routes' if reader_name == 'positions' else 'positions')(source['source_key'], limit=1)['next_cursor']
    for malformed in ('{', 'null', '{}', '[]', '"legacy"', wrong_kind):
        with pytest.raises(ValueError) as rejected:
            reader(source['source_key'], after=malformed)
        assert rejected.value.code == 'invalid_cursor'
    with pytest.raises(ValueError) as rejected:
        reader(other['source_key'], after=token)
    assert rejected.value.code == 'invalid_cursor'


def test_stale_revision_is_rejected_before_any_input_staging(service, tmp_path):
    source = source_fixture()
    first, _ = publish(service, source)
    source['title'] = 'Current successor'
    current = service.load_source(source, expected_revision=first['source_revision'])
    finish(service, source['source_key'])
    publication = service.publication_build(source['source_key'])
    before = staging_state()
    for index in range(3):
        stale = deepcopy(source)
        stale['title'] = 'Rejected distinct edit ' + str(index)
        fixture = tmp_path / 'stale.json'
        fixture.write_text(json.dumps(stale))
        result = subprocess.run([str(Path(sys.executable).with_name('tempo-content')), 'source', 'load', str(fixture),
                                 '--expected-revision', str(first['source_revision'])], cwd=tmp_path,
                                env=cli_environment(), capture_output=True, text=True, timeout=20)
        assert result.returncode == 2 and result.stdout == ''
        assert json.loads(result.stderr)['code'] == 'stale_source_revision'
    assert staging_state() == before
    assert service.inventory_status(source['source_key'])['generation'] == current['generation']
    assert serialize_contract(service.publication_build(source['source_key'])) == serialize_contract(publication)


def test_identical_source_replay_with_old_expected_revision_is_idempotent(service):
    source = source_fixture()
    first, _ = publish(service, source)
    source['title'] = 'Current'
    second = service.load_source(source, expected_revision=first['source_revision'])
    finish(service, source['source_key'])
    publication = service.publication_build(source['source_key'])
    before = staging_state()
    replay = service.load_source(source, expected_revision=0,
                                 hook=lambda event, context: pytest.fail('identical replay must not stage input'))
    assert replay['replayed'] and replay['generation'] == second['generation']
    assert replay['source_revision'] == second['source_revision']
    assert staging_state() == before
    assert service.publication_build(source['source_key']) == publication


def test_unknown_source_wrong_initial_revision_rejects_without_staging(service):
    source = source_fixture()
    before = staging_state()
    with pytest.raises(ValueError) as rejected:
        service.load_source(source, expected_revision=2)
    assert rejected.value.code == 'stale_source_revision'
    assert staging_state() == before
    assert not query('SELECT 1 FROM content.sources WHERE source_key=%s', (source['source_key'],))


def test_concurrent_distinct_source_edits_admit_only_one_revision(service):
    source = source_fixture()
    first, _ = publish(service, source)
    staged = Barrier(2)
    edits = []
    for label in ('editor-a', 'editor-b'):
        edit = deepcopy(source)
        edit['title'] = label
        edit['exercises'][0]['prompt'] = label
        edits.append(edit)

    def admit(edit):
        def hook(event, context):
            if event == 'after_staging':
                staged.wait(timeout=10)
        with reopen() as editor:
            try:
                return edit, editor.load_source(edit, expected_revision=first['source_revision'], hook=hook)
            except ValueError as error:
                return edit, error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(admit, edit) for edit in edits]
        outcomes = [future.result(timeout=20) for future in futures]
    winners = [(edit, result) for edit, result in outcomes if isinstance(result, dict)]
    losers = [result for edit, result in outcomes if not isinstance(result, dict)]
    assert len(winners) == len(losers) == 1 and losers[0].code == 'stale_source_revision'
    winner, admitted = winners[0]
    assert admitted['source_revision'] == first['source_revision'] + 1
    assert admitted['generation'] == first['generation'] + 1
    assert service.source_show(source['source_key'])['snapshot'] == SourceSnapshot.model_validate(winner).model_dump(mode='json')
    assert query('SELECT count(*) FROM content.tasks WHERE source_id=%s', (first['source_id'],)) == [(2,)]
    finish(service, source['source_key'])
    publication = service.publication_build(source['source_key'])
    assert publication.publication_generation == admitted['generation']
    assert winner['title'] in serialize_contract(publication)
    assert next(edit['title'] for edit in edits if edit != winner) not in serialize_contract(publication)


def cli_environment():
    return {name: value for name, value in os.environ.items()
            if name in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT', 'TEMPO_CONTENT_DATABASE_URL', 'TEMPO_CONTENT_WORKSPACE'}}


def repertoire_source(key):
    source = source_fixture()
    source['repertoires'][0]['key'] = key
    source['policies'][0]['repertoire_key'] = key
    source['memberships'][0]['repertoire_key'] = key
    return source


def test_unscoped_repertoire_lookup_rejects_ambiguous_source_keys(service, tmp_path):
    key = 'repertoire-' + uuid.uuid4().hex
    sources = [repertoire_source(key), repertoire_source(key)]
    for source in sources:
        service.load_source(source)
    with pytest.raises(ValueError) as rejected:
        service.repertoire_show(key)
    assert rejected.value.code == 'ambiguous_repertoire' and '--source-key' in str(rejected.value)
    results = [service.repertoire_show(key, source['source_key']) for source in sources]
    assert {row['source_key'] for row in results} == {source['source_key'] for source in sources}
    assert len({row['repertoire_id'] for row in results}) == 2
    for source, result in zip(sources, results):
        assert {field: result[field] for field in ('key', 'title', 'learner_color')} == source['repertoires'][0]
    result = subprocess.run([str(Path(sys.executable).with_name('tempo-content')), 'repertoire', 'show', key],
                            cwd=tmp_path, env=cli_environment(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and result.stdout == ''
    assert json.loads(result.stderr)['code'] == 'ambiguous_repertoire'


def test_unique_and_missing_repertoire_lookups(service):
    key = 'unique-' + uuid.uuid4().hex
    source = repertoire_source(key)
    service.load_source(source)
    assert service.repertoire_show(key) == service.repertoire_show(key, source['source_key'])
    for scope in (None, source['source_key']):
        with pytest.raises(ValueError) as rejected:
            service.repertoire_show('absent-' + key, scope)
        assert rejected.value.code == 'repertoire_not_found'


@pytest.mark.parametrize('table,privilege', [('input_items', 'INSERT'), ('tasks', 'UPDATE'), ('responses', 'SELECT')],
                         ids=['missing_input_insert', 'missing_task_update', 'missing_required_select'])
def test_runtime_readiness_rejects_missing_required_grant_and_owner_repairs_it(service, table, privilege):
    owner = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
    role = os.environ['TEMPO_CONTENT_RUNTIME_ROLE']
    before = query('SELECT id,checksum FROM content.migration_history ORDER BY id')
    try:
        with psycopg.connect(owner) as database:
            database.execute(sql.SQL('REVOKE {} ON content.{} FROM {}').format(sql.SQL(privilege), sql.Identifier(table), sql.Identifier(role)))
        with pytest.raises(ValueError, match=table + '.*' + privilege):
            reopen()
    finally:
        migrate(owner, os.environ['TEMPO_CONTENT_WORKSPACE'], role)
    migrate(owner, os.environ['TEMPO_CONTENT_WORKSPACE'], role)
    assert query('SELECT id,checksum FROM content.migration_history ORDER BY id') == before
    assert query('SELECT has_table_privilege(current_user,%s,%s)', ('content.' + table, privilege)) == [(True,)]
    assert query("SELECT has_table_privilege(current_user,'content.source_revisions','UPDATE')") == [(False,)]
    with reopen() as repaired:
        source = source_fixture()
        repaired.load_source(source)
        finish(repaired, source['source_key'])
        assert repaired.publication_build(source['source_key'])


@pytest.mark.parametrize('missing', ['occurrence', 'response_occurrence', 'reply', 'response', 'membership', 'task'])
def test_coverage_rejects_missing_mandatory_complete_inventory(service, missing):
    source = covered_source()
    loaded, _ = publish(service, source)
    artifact = query('SELECT artifact_id FROM content.generation_routes WHERE source_id=%s LIMIT 1', (loaded['source_id'],))[0][0]
    position = query('SELECT position_key FROM content.occurrences WHERE artifact_id=%s AND ply=1', (artifact,))[0][0]
    # Each source has its own artifact; canonical legal facts are shared and
    # must be restored after the separate runtime connection observes corruption.
    statements = {
        'occurrence': ('DELETE FROM content.occurrences WHERE artifact_id=%s AND ply=1', (artifact,)),
        'response_occurrence': ('DELETE FROM content.occurrences WHERE artifact_id=%s AND ply=2', (artifact,)),
        'reply': ('DELETE FROM content.legal_replies WHERE position_key=%s AND move_uci=%s', (position, 'e7e5')),
        'response': ('DELETE FROM content.responses WHERE source_id=%s AND generation=%s AND ply=2', (loaded['source_id'], loaded['generation'])),
        'membership': ('DELETE FROM content.generation_routes WHERE source_id=%s AND generation=%s', (loaded['source_id'], loaded['generation'])),
    }
    with psycopg.connect(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']) as database:
        if missing == 'task':
            database.execute('DELETE FROM content.generation_routes WHERE source_id=%s', (loaded['source_id'],))
            database.execute('DELETE FROM content.generation_exercises WHERE source_id=%s', (loaded['source_id'],))
            database.execute('DELETE FROM content.tasks WHERE source_id=%s', (loaded['source_id'],))
        else:
            database.execute(*statements[missing])
    try:
        with pytest.raises(ValueError) as rejected:
            service.coverage(source['source_key'], 'e4', 'main', 1)
        assert rejected.value.code == 'inventory_incomplete'
    finally:
        if missing == 'reply':
            # Legal replies are canonical shared facts; restore the deleted fact.
            board = chess.Board(position + ' 0 1')
            board.push_uci('e7e5')
            with psycopg.connect(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']) as database:
                database.execute('INSERT INTO content.legal_replies VALUES(%s,%s,%s)', (position, 'e7e5', canonical_position(board.fen())))
        # Corrupt fixture sources must not contaminate subsequent global workers.
        with psycopg.connect(os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']) as database:
            database.execute("UPDATE content.tasks SET state='failed' WHERE source_id=%s", (loaded['source_id'],))


def test_inventory_read_snapshot_is_read_only_and_rolls_back_on_error(service):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with service._read_snapshot() as database:
            assert database.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] == 'repeatable read'
            assert database.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
            database.execute('UPDATE content.collector SET pending=pending WHERE id=1')
    # Reuse the same pool after failure: read-only transaction settings are local.
    with service._transaction() as database:
        assert database.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'off'
        database.execute('UPDATE content.collector SET pending=pending WHERE id=1')


@pytest.mark.parametrize('cursor', ['{', pytest.param('['*20000+']'*20000, id='excessive-nesting')])
def test_inventory_cli_rejects_invalid_cursor_as_structured_error(service, tmp_path, cursor):
    source = source_fixture()
    publish(service, source)
    result = subprocess.run([str(Path(sys.executable).with_name('tempo-content')), 'inventory', 'positions',
                             source['source_key'], '--after', cursor], cwd=tmp_path, env=cli_environment(),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and result.stdout == ''
    assert json.loads(result.stderr)['code'] == 'invalid_cursor'


def test_A_semantic_draft_collision_rejects_changed_raw_provenance(service):
    original = bridge_draft(service)
    manifest = deepcopy(original["document"]["manifest"])
    manifest["provenance"]["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="immutable.*provenance"):
        Bridge(service).draft(original["document"]["source"], manifest, 0)
    assert Bridge(service).draft_show(original["id"])["document"] == original["document"]


def test_A_exact_draft_replay_returns_original_immutable_document(service):
    original = bridge_draft(service)
    with reopen() as restarted:
        replay = Bridge(restarted).draft(original["document"]["source"], original["document"]["manifest"], 0)
    assert replay == original


@pytest.mark.parametrize("conflicting", [False, True])
def test_A_concurrent_proposal_identity_is_unique_across_sessions(service, conflicting):
    original = bridge_draft(service)
    proposal = {"workspace_id": service.workspace_id, "source_key": original["source_key"], "proposal_id": "concurrent"}
    barrier = Barrier(2)
    def insert(index):
        candidate = deepcopy(proposal)
        candidate["accepted_lines"] = [["d2d4" if index and conflicting else "e2e4"]]
        with reopen() as application:
            barrier.wait(timeout=5)
            try:
                return Bridge(application).draft(original["document"]["source"], original["document"]["manifest"], 0, candidate)
            except ValueError as error:
                return error
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert, [0, 1]))
    rows = query("SELECT id FROM content.bridge_drafts WHERE source_key=%s AND document->'proposal'->>'proposal_id'='concurrent'", (original["source_key"],))
    assert len(rows) == 1
    if conflicting:
        assert sum(isinstance(result, ValueError) for result in results) == 1
        assert "Proposal idempotency" in str(next(result for result in results if isinstance(result, ValueError)))
    else:
        assert results[0]["id"] == results[1]["id"]


def failure_fixture(service):
    bridge = Bridge(service)
    draft = bridge_draft(service)
    operation = bridge.prepare(bridge.approve(draft['id'], draft['digest'])['id'], 100)
    request = {'activation_id': operation['id'] + ':activate', 'publication_id': operation['document']['export']['publication']['publication_id'],
        'publication_generation': operation['document']['export']['publication']['publication_generation'], 'expected_activation_revision': 0}
    delivery = {'endpoint': 'http://127.0.0.1:8001', 'retirement': False, 'request': request, 'due_date': '2026-10-16'}
    operation = bridge.checkpoint(operation['id'], {'delivery': delivery})
    failure = {'transport': 'authenticated_learning_http', 'endpoint': delivery['endpoint'], 'content_operation_id': operation['id'],
        'learning_operation_id': request['activation_id'], 'workspace_id': service.workspace_id, 'state': 'failed',
        'delivery': delivery, 'error': {'status_code': 409}, 'receipt_sha256': 'a' * 64}
    return operation, failure


def test_A_terminal_learning_rejection_releases_source_reservation(service):
    operation, failure = failure_fixture(service)
    bridge = Bridge(service)
    assert bridge.record_terminal_failure(operation['id'], failure)['state'] == 'failed'
    revision = service.source_show(operation['source_key'])['requested_revision']
    assert revision == 1
    corrected = bridge.draft(operation['draft']['source'], operation['draft']['manifest'], revision)
    successor = bridge.approve(corrected['id'], corrected['digest'])
    with reopen() as restarted:
        assert Bridge(restarted).record_terminal_failure(operation['id'], failure)['document']['failure'] == failure
        assert Bridge(restarted).operation(successor['id'])['state'] == 'approved'
        with pytest.raises(ValueError):
            Bridge(restarted).prepare(operation['id'])
    assert query("SELECT id FROM content.bridge_operations WHERE source_id=%s AND state IN ('approved','applying')", (operation['source_id'],)) == [(successor['id'],)]


@pytest.mark.parametrize('conflict', ['completed', 'failure_bytes', 'stale_success'])
def test_A_terminal_outcomes_cannot_overwrite_each_other(service, conflict):
    bridge = Bridge(service)
    operation, failure = failure_fixture(service)
    publication = operation['document']['export']['publication']
    receipt = {'transport': 'authenticated_learning_http', 'endpoint': failure['endpoint'], 'acknowledgement': {
        'contract_version': 1, 'workspace_id': service.workspace_id, 'activation_id': operation['id'] + ':activate',
        'publication_id': publication['publication_id'], 'publication_generation': publication['publication_generation'],
        'activation_revision': 1, 'status': 'activated', 'reason': None}}
    if conflict == 'completed':
        bridge.record(operation['id'], receipt)
        with pytest.raises(ValueError):
            bridge.record_terminal_failure(operation['id'], failure)
        assert bridge.operation(operation['id'])['state'] == 'completed'
    else:
        bridge.record_terminal_failure(operation['id'], failure)
        with pytest.raises(ValueError):
            if conflict == 'failure_bytes':
                bridge.record_terminal_failure(operation['id'], {**failure, 'receipt_sha256': 'b' * 64})
            else:
                bridge.record(operation['id'], receipt)
        assert bridge.operation(operation['id'])['document']['failure'] == failure


def test_A_terminal_failure_and_corrected_approval_serialize(service):
    operation, failure = failure_fixture(service)
    bridge = Bridge(service)
    corrected = bridge.draft(operation['draft']['source'], operation['draft']['manifest'], 1)
    barrier = Barrier(3)
    def reconcile():
        with reopen() as application:
            barrier.wait(timeout=5)
            return Bridge(application).record_terminal_failure(operation['id'], failure)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(reconcile), executor.submit(reconcile)
        barrier.wait(timeout=5)
        try:
            approved = bridge.approve(corrected['id'], corrected['digest'])
        except ValueError as error:
            assert 'approved operation' in str(error)
            approved = None
        assert first.result()['state'] == second.result()['state'] == 'failed'
    approved = approved or bridge.approve(corrected['id'], corrected['digest'])
    assert approved['state'] == 'approved'
    assert query("SELECT count(*) FROM content.bridge_operations WHERE source_id=%s AND state IN ('approved','applying')", (operation['source_id'],))[0][0] == 1


def test_A_approved_draft_cannot_be_relabelled_by_reimport(service):
    original = bridge_draft(service)
    Bridge(service).approve(original['id'], original['digest'])
    manifest = deepcopy(original['document']['manifest'])
    manifest['provenance']['source_sha256'] = 'e' * 64
    with reopen() as restarted, pytest.raises(ValueError, match='immutable.*provenance'):
        Bridge(restarted).draft(original['document']['source'], manifest, 0)
    assert Bridge(service).draft_show(original['id']) == original


@pytest.mark.parametrize('conflicting', [False, True])
def test_A_response_proposal_concurrency_and_restart(service, conflicting):
    operation, _ = failure_fixture(service)
    bridge = Bridge(service)
    publication = operation['document']['export']['publication']
    # Component-local completion fixture; installed cross-service tests separately
    # establish real authenticated Learning receipts.
    bridge.record(operation['id'], {'transport':'authenticated_learning_http', 'endpoint':'http://127.0.0.1:8001', 'acknowledgement': {
        'contract_version':1, 'workspace_id':service.workspace_id, 'activation_id':operation['id']+':activate',
        'publication_id':publication['publication_id'], 'publication_generation':publication['publication_generation'],
        'activation_revision':1, 'status':'activated', 'reason':None}})
    source = service.source_show(operation['source_key'])
    board = chess.Board(source['snapshot']['routes'][0]['starting_fen'])
    board.push_uci('e2e4'); board.push_uci('c7c5')
    proposal = {'schema_version':1, 'kind':'response_proposal', 'status':'draft', 'proposal_id':'response-race',
        'workspace_id':service.workspace_id, 'source_key':operation['source_key'], 'repertoire_key':'white', 'policy_key':'main',
        'learner_color':'white', 'expected_source_revision':source['requested_revision'],
        'source_digest':operation['document']['export']['source']['source_digest'], 'route_key':'e4', 'anchor_ply':1,
        'opponent_move':'c7c5', 'decision_fen':board.fen(en_passant='fen'), 'accepted_lines':[['g1f3']],
        'provenance':{'kind':'synthetic','evidence_refs':['game:race']}}
    barrier = Barrier(2)
    def propose(index):
        candidate = deepcopy(proposal)
        if conflicting and index:
            candidate['accepted_lines'] = [['b1c3']]
        with reopen() as application:
            original_one = application._one
            def synchronized_read(statement, parameters=()):
                result = original_one(statement, parameters)
                if statement.startswith('SELECT id,document FROM content.bridge_drafts'):
                    barrier.wait(timeout=5)
                return result
            application._one = synchronized_read
            try:
                return Bridge(application).proposal_draft(candidate)
            except ValueError as error:
                return error
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(propose, [0, 1]))
    winners = [result for result in results if isinstance(result, dict)]
    assert len(winners) == (1 if conflicting else 2)
    assert len({winner['id'] for winner in winners}) == 1
    winner = winners[0]
    with reopen() as restarted:
        assert Bridge(restarted).proposal_draft(winner['document']['proposal']) == winner
        changed = deepcopy(winner['document']['proposal'])
        changed['accepted_lines'] = [['h2h3']]
        with pytest.raises(ValueError, match='Proposal idempotency'):
            Bridge(restarted).proposal_draft(changed)
    assert query("SELECT count(*) FROM content.bridge_drafts WHERE source_key=%s AND document->'proposal'->>'proposal_id'='response-race'", (operation['source_key'],))[0][0] == 1


def test_A_proposal_unique_index_rejects_legacy_duplicates_without_deletion(service):
    from tempo_content.migrate import expected_migrations
    original = bridge_draft(service)
    proposal = {'workspace_id':service.workspace_id, 'source_key':original['source_key'], 'proposal_id':'legacy-duplicate'}
    first = Bridge(service).draft(original['document']['source'], original['document']['manifest'], 0, proposal)
    owner_url = os.environ['TEMPO_CONTENT_MIGRATION_DATABASE_URL']
    with psycopg.connect(owner_url) as database:
        database.execute('DROP INDEX content.bridge_proposal_identity')
        database.execute('INSERT INTO content.bridge_drafts SELECT %s,source_id,source_key,expected_revision,digest,document FROM content.bridge_drafts WHERE id=%s', ('duplicate-'+first['id'],first['id']))
        with pytest.raises(psycopg.errors.UniqueViolation), database.transaction():
            database.execute("CREATE UNIQUE INDEX bridge_proposal_identity ON content.bridge_drafts ((document->'proposal'->>'workspace_id'),source_key,(document->'proposal'->>'proposal_id')) WHERE jsonb_typeof(document->'proposal')='object'")
        assert database.execute("SELECT count(*) FROM content.bridge_drafts WHERE source_key=%s AND document->'proposal'->>'proposal_id'='legacy-duplicate'", (original['source_key'],)).fetchone()[0] == 2
        database.rollback()
    assert '0004_bridge_proposal_identity' in expected_migrations()
    assert query("SELECT count(*) FROM content.bridge_drafts WHERE source_key=%s AND document->'proposal'->>'proposal_id'='legacy-duplicate'", (original['source_key'],))[0][0] == 1


def test_A_success_and_terminal_failure_race_preserves_one_outcome(service):
    operation, failure = failure_fixture(service)
    publication = operation['document']['export']['publication']
    receipt = {'transport':'authenticated_learning_http', 'endpoint':failure['endpoint'], 'acknowledgement': {
        'contract_version':1, 'workspace_id':service.workspace_id, 'activation_id':operation['id']+':activate',
        'publication_id':publication['publication_id'], 'publication_generation':publication['publication_generation'],
        'activation_revision':1, 'status':'activated', 'reason':None}}
    barrier = Barrier(2)
    def record(success):
        with reopen() as application:
            barrier.wait(timeout=5)
            try:
                bridge = Bridge(application)
                return bridge.record(operation['id'], receipt) if success else bridge.record_terminal_failure(operation['id'], failure)
            except ValueError as error:
                return error
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(record, [True, False]))
    assert sum(isinstance(result, dict) for result in results) == 1
    result = Bridge(service).operation(operation['id'])
    assert result['state'] in ('completed', 'failed')
    assert ('receipt' in result['document']) != ('failure' in result['document'])


def test_A_proposal_identity_is_scoped_to_workspace_and_source(service):
    first, second = bridge_draft(service), bridge_draft(service)
    for original in (first, second):
        proposal = {'workspace_id':service.workspace_id,'source_key':original['source_key'],'proposal_id':'same-permitted-id'}
        draft = Bridge(service).draft(original['document']['source'],original['document']['manifest'],0,proposal)
        assert draft['document']['proposal'] == proposal
    assert query("SELECT count(*) FROM content.bridge_drafts WHERE source_key=ANY(%s) AND document->'proposal'->>'proposal_id'='same-permitted-id'", ([first['source_key'],second['source_key']],))[0][0] == 2
