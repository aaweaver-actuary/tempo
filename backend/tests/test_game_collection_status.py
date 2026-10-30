"""Read-only collection observations; all game/account values are synthetic."""
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import uuid

import pytest

from app.product_composition.game_collection_status import MAX_OUTPUT_BYTES, _encode, main

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/game-review-contracts/src", "packages/chess-core/src",
               "packages/evidence-math/src", "packages/scheduling/src", "packages/game-classification/src",
               "packages/game-ingestion/src", "packages/repertoire-comparison/src",
               "contracts/game-evidence/src", "contracts/analysis-jobs/src",
               "services/learning/application/src", "services/learning/runtime/src",
               "services/learning/remediation/src"):
    sys.path.insert(0, str(ROOT / source))

from tempo_legacy_database.adapters.games.collection_observations import CollectionObservations, retained_summaries
from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
from test_product_game_lapse_postgres import owned_postgres
from test_product_game_review_receipts import scenario


def finding(*, kind="known_unintroduced", ply=1, repertoire="white", color="white", matched=False,
            membership="present"):
    value = json.loads((ROOT / "contracts/product-v02/fixtures/confirmed-introduced-miss.json").read_text())["payload"]
    value.update(kind=kind, ply=ply, repertoire_id=repertoire, repertoire_revision=1,
                 expected_uci=["e2e4"] if kind not in {"coverage_end", "missing_own_continuation"} else [],
                 played_uci="e2e4" if matched else "d2d4", membership=membership,
                 position_key="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR " + ("w" if color == "white" else "b") + " KQkq -",
                 lapse_eligible=False, diagnostic="synthetic_retained_finding")
    return value


@pytest.fixture(scope="module")
def reader_database(owned_postgres):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    with owned_postgres() as database:
        role = "task_f_reader_" + uuid.uuid4().hex[:12]
        password = uuid.uuid4().hex
        database.execute_native(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(password)))
        database.execute_native(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        tables = ("product_feedback_configuration", "product_feedback_jobs", "product_feedback_sources",
                  "product_feedback_captures", "product_feedback_deliveries", "product_feedback_sync_requests",
                  "imported_games", "tempo_schema_migrations")
        database.execute_native(sql.SQL("GRANT SELECT ON {} TO {}").format(
            sql.SQL(",").join(map(sql.Identifier,tables)),sql.Identifier(role)))
        info = database.raw.info
        dsn = make_conninfo(host=info.host,port=info.port,dbname=info.dbname,user=role,password=password)
    return SimpleNamespace(admin=owned_postgres,dsn=dsn,tables=tables,role=role)


@pytest.fixture
def stored(reader_database):
    from psycopg.types.json import Jsonb
    identity = uuid.uuid4().hex
    source = scenario().source.model_dump(mode="json")
    source.update(source_key=identity, findings=[finding()], diagnostic=None)
    source["normalized"].update(workspace_id=identity,source_id="chess.com",game_id=identity,learner_color="white")
    source["original"]["source_game_id"] = identity
    spec = dict(job_id=identity,workspace_id=identity,provider="chesscom",account="private-account-"+identity,
                window=dict(start_at="2026-10-01T00:00:00Z",end_at="2026-10-02T00:00:00Z",precision="second"),
                repertoire_ids=["white"],filters=[])
    with reader_database.admin() as database:
        database.execute_native("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s)",
                                (identity,Jsonb(source)))
        database.execute_native("INSERT INTO product_feedback_jobs(job_id,version,status,next_attempt_at,lease_until,attempts,payload) VALUES(%s,1,'queued',0,0,0,%s)",
                                (identity,Jsonb(dict(spec=spec,window_complete=False,pages_committed=1,continuation="private-cursor"))))
        database.execute_native("INSERT INTO product_feedback_sync_requests(workspace_id,operation_id,provider,job_id,requested_at) VALUES(%s,%s,'chesscom',%s,1)",(identity,identity,identity))
    connections = DedicatedGamesConnections(reader_database.dsn,read_only=True)
    return SimpleNamespace(identity=identity,source=source,spec=spec,reader=CollectionObservations(connections),
                           connections=connections,database=reader_database)


@pytest.mark.parametrize("color", ["white", "black"])
@pytest.mark.parametrize("kind", ["known_unintroduced", "known_unavailable", "ambiguous"])
def test_retained_own_miss_does_not_require_card_or_chronology(color,kind):
    miss = finding(color=color,kind=kind)
    miss.update(presentation_id=None,presentation_revision=None,introduced_before_game=None)
    result, = retained_summaries([miss], color)
    assert result["first_departure"] == result["first_own_response_miss"] == miss


@pytest.mark.parametrize("kind", ["opponent_gap", "missing_own_continuation", "coverage_end", "conflict"])
def test_boundary_kinds_never_become_own_misses(kind):
    boundary = finding(kind=kind,color="black" if kind=="opponent_gap" else "white")
    result, = retained_summaries([boundary],"white")
    assert result["first_departure"] == boundary
    assert result["first_own_response_miss"] is None


def test_first_departure_is_per_repertoire_and_survives_reentry():
    correct = finding(matched=True)
    gap = finding(kind="opponent_gap",ply=2,color="black")
    later = finding(ply=3)
    other = finding(ply=5,repertoire="second")
    a,b = retained_summaries([other,later,gap,correct],"white")
    assert a["first_departure"] == gap and a["first_own_response_miss"] == later
    assert b["first_departure"] == other


def test_native_first_boundary_capture_is_atomic_and_reader_retains_selected_global_result(reader_database):
    """Existing Games page commit persists election with source/capture/outbox."""
    import psycopg
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from test_product_feedback_jobs import encounter_harness, spec
    from app.games.feedback.coordinator import stable_id

    @contextmanager
    def connections():
        with reader_database.admin() as database:
            yield database.raw

    store = PostgresGamesStore(connections)
    current = encounter_harness(store)
    request = spec("first-boundary-" + uuid.uuid4().hex)
    store.enqueue(request)
    claimed = store.claim_job(current.clock.now(), current.clock.now() + 120)
    source_key = stable_id("fixture", "lichess", "provider-game")
    with connections() as database:
        database.execute("""CREATE FUNCTION reject_first_boundary_delivery() RETURNS trigger LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'first_boundary_atomicity_fault'; END $$""")
        database.execute("""CREATE TRIGGER first_boundary_atomicity BEFORE INSERT ON product_feedback_deliveries
            FOR EACH ROW EXECUTE FUNCTION reject_first_boundary_delivery()""")
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="first_boundary_atomicity_fault"):
            current.coordinator.capture_page(claimed)
        assert store.source(source_key) is None
        assert store.load_job(request.job_id).last_checkpoint_at is None
        with connections() as database:
            assert database.execute("SELECT COUNT(*) FROM product_feedback_captures WHERE source_key=%s", (source_key,)).fetchone()[0] == 0
    finally:
        with connections() as database:
            database.execute("DROP TRIGGER first_boundary_atomicity ON product_feedback_deliveries")
            database.execute("DROP FUNCTION reject_first_boundary_delivery()")
    current.coordinator.capture_page(claimed)
    restored = PostgresGamesStore(connections)
    source = restored.source(source_key)
    assert source.first_departure.status == "own_actionable" and source.first_departure.ply == 1
    assert source.determined_at == current.clock.utc_now()
    pending = restored.pending_delivery(current.clock.now())
    assert pending.encounter.key.decision_id == source.first_departure.decision.decision_id
    with connections() as database:
        capture = database.execute("SELECT payload FROM product_feedback_captures WHERE source_key=%s", (source_key,)).fetchone()[0]
    assert capture["first_departure"] == source.first_departure.model_dump(mode="json")
    assert capture["determined_at"] == source.determined_at
    reader = CollectionObservations(DedicatedGamesConnections(reader_database.dsn, read_only=True))
    detail = reader.selected_game("fixture", "lichess", "provider-game")
    assert detail["first_departure"] == capture["first_departure"]
    assert detail["determined_at"] == source.determined_at and detail["selection_diagnostic"] is None
    assert detail["comparison"] == "retained_evidence"
    assert detail["repertoires"][0]["first_own_response_miss"] is not None
    current.clock.value += 60
    repeated = spec("first-boundary-retry-" + uuid.uuid4().hex)
    store.enqueue(repeated)
    current.coordinator.capture_page(store.load_job(repeated.job_id))
    assert restored.source(source_key).determined_at == source.determined_at
    assert restored.pending_delivery(current.clock.now()).encounter == pending.encounter


def test_new_selection_redacts_route_scope_and_retains_finite_policy_diagnostics():
    from test_product_first_boundary import select
    selection = select().model_dump(mode="json")
    encoded = _encode(dict(schema_version=1, identity={}, first_departure=selection))
    value = json.loads(encoded)["first_departure"]
    assert value["status"] == "own_actionable" and value["policy_version"] == "first_departure_v1"
    assert value["selected_scope"][0]["snapshot_id"].startswith("redacted:")
    assert value["selected_scope"][0]["source_revision"].startswith("redacted:")
    assert value["surviving_routes"][0]["route_id"].startswith("redacted:")
    assert value["surviving_routes"][0]["policy_id"].startswith("redacted:")
    assert value["decision"]["position_key"].startswith("redacted:")
    assert value["decision"]["selected_response_uci"].startswith("redacted:")
    assert "source-rep" not in encoded and "card-route" not in encoded and "e2e4" not in encoded
    assert json.loads(_encode(dict(identity={}, diagnostic="selected_scope_incomplete_or_unavailable")))["diagnostic"] == "selected_scope_incomplete_or_unavailable"


def test_unavailable_authored_snapshot_is_not_an_own_miss():
    result, = retained_summaries([finding(kind="known_unavailable",membership="unknown")],"white")
    assert result["first_own_response_miss"] is None


def test_correct_physical_mapping_ambiguity_is_not_a_departure():
    result, = retained_summaries([finding(kind="ambiguous",matched=True)],"white")
    assert result["first_departure"] is None and result["first_own_response_miss"] is None


def test_native_reader_permissions_and_fixture_rows_unchanged(stored):
    def snapshot():
        with stored.database.admin() as database:
            return [[row[0] for row in database.execute_native("SELECT row_to_json(record)::text FROM "+table+" record ORDER BY 1").fetchall()]
                    for table in stored.database.tables if table.startswith("product_")]
    before = snapshot()
    report = stored.reader.report(workspace=stored.identity,provider="chesscom",game_id=stored.identity)
    assert report["identity"]["reader_role"] == stored.database.role
    assert report["identity"]["transaction_read_only"] == "on"
    assert all(report[k]["availability"]=="available" for k in ["identity","providers","collection","delivery","selected_game"]),report
    assert snapshot() == before
    permissions = stored.reader._rows("SELECT has_table_privilege(current_user,'product_feedback_sources','INSERT,UPDATE,DELETE') writes")
    assert permissions == [{"writes":False}]
    import psycopg
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        stored.reader._rows("UPDATE product_feedback_sources SET version=version WHERE false RETURNING version")


def test_duplicate_captures_do_not_inflate_unique_games_or_misses(stored):
    from psycopg.types.json import Jsonb
    with stored.database.admin() as database:
        for revision in ["capture-a","capture-b"]:
            database.execute_native("INSERT INTO product_feedback_captures(source_key,source_revision,source_digest,comparison_revision,payload) VALUES(%s,1,'digest',%s,%s)",
                                    (stored.identity,revision,Jsonb({"synthetic":"capture"})))
        alias = deepcopy(stored.source)
        alias["normalized"]["source_id"]="chesscom"
        database.execute_native("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,1,%s)",
                                (stored.identity+"alias",Jsonb(alias)))
    collection=stored.reader.collection(stored.identity,20,"chesscom")
    assert collection["unique_games"] == collection["unique_own_response_misses"] == 1,collection


@pytest.mark.parametrize("provider",["chesscom","chess.com"])
def test_public_command_reads_both_provider_namespace_spellings(stored,provider,capsys):
    assert main(["--workspace",stored.identity,"--provider",provider,"--game-id",stored.identity,"--private"],reader_factory=lambda:stored.reader)==0
    report=json.loads(capsys.readouterr().out)
    assert report["selected_game"]["provider"] == "chesscom"
    assert report["selected_game"]["canonical_game_id"] == stored.identity


def test_pending_job_without_configuration_is_visible(stored):
    item,=stored.reader.providers(stored.identity,20,"chesscom")["items"]
    assert item["configured"] is False and not any(item["required_inputs"].values())
    assert item["current_jobs"][0]["job_id"]==stored.identity
    assert item["current_jobs"][0]["continuation_present"] is True


def test_saved_configuration_and_selected_readiness_preserve_explicit_null_policy(stored):
    from psycopg.types.json import Jsonb
    configuration={key:stored.spec[key] for key in ["workspace_id","provider","account","window","repertoire_ids"]}
    configuration.update(speeds=["rapid"],rated=None,interval_seconds=None,revision=1)
    with stored.database.admin() as database:
        database.execute_native("INSERT INTO product_feedback_configuration(workspace_id,provider,revision,next_run_at,payload) VALUES(%s,'chesscom',1,0,%s)",
                                (stored.identity,Jsonb(configuration)))
    selected=[]
    def readiness(repertoire):
        selected.append(repertoire)
        return dict(availability="unavailable",diagnostic="canonical_prefix_snapshot_unsupported")
    stored.reader.repertoire_readiness=readiness
    item,=stored.reader.providers(stored.identity,20,"chesscom")["items"]
    assert item["configuration"]==configuration and all(item["required_inputs"].values())
    assert selected==["white"]
    assert item["repertoire_readiness"][0]["diagnostic"]=="canonical_prefix_snapshot_unsupported"
    assert "overlap_seconds" not in item["configuration"]


def test_empty_findings_and_unavailable_snapshot_stay_unresolved(stored):
    from psycopg.types.json import Jsonb
    source=deepcopy(stored.source)
    source.update(findings=[],diagnostic='comparison_unavailable:["white"]')
    with stored.database.admin() as database:
        database.execute_native("UPDATE product_feedback_sources SET payload=%s WHERE source_key=%s",(Jsonb(source),stored.identity))
    assert stored.reader.collection(stored.identity,20,None)["unresolved_games"]==1
    assert stored.reader.selected_game(stored.identity,"chesscom",stored.identity)["comparison"]=="unresolved"


def test_missing_receipt_is_undelivered_without_collection_failure(stored):
    from psycopg.types.json import Jsonb
    delivery=scenario().delivery.model_dump(mode="json")
    delivery.update(operation_id=stored.identity,source_key=stored.identity,state="pending")
    with stored.database.admin() as database:
        database.execute_native("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'encounter','pending',0,%s)",
                                (stored.identity,stored.identity,stored.identity,Jsonb(delivery)))
    owner=SimpleNamespace(encounter_status=lambda key:None,effect_status=lambda key:None)
    stored.reader.receipts=lambda:owner
    report=stored.reader.report(workspace=stored.identity,provider="chesscom",game_id=stored.identity)
    assert report["collection"]["availability"]=="available"
    assert report["delivery"]["pending_undelivered_effects"]==1
    assert report["selected_game"]["receipts"][0]["diagnostic"]=="not_delivered"


def test_control_of_original_encounter_reads_encounter_receipt(stored):
    from psycopg.types.json import Jsonb
    case=scenario(control=True)
    delivery=case.delivery.model_dump(mode="json")
    delivery.update(operation_id=stored.identity,source_key=stored.identity)
    with stored.database.admin() as database:
        database.execute_native("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'control','acknowledged',0,%s)",
                                (stored.identity,stored.identity,stored.identity,Jsonb(delivery)))
    calls=[]
    def encounter_status(key):
        calls.append(key)
        return case.receipt
    def forbidden(key):pytest.fail("encounter control used legacy receipt status")
    stored.reader.receipts=lambda:SimpleNamespace(encounter_status=encounter_status,effect_status=forbidden)
    detail=stored.reader.selected_game(stored.identity,"chesscom",stored.identity)
    assert calls==[case.command.key]
    assert detail["receipts"][0]["receipt"]["admission_status"]=="pending"


def test_unavailable_reads_do_not_fabricate_zero_counts():
    class Unavailable:
        read_only=True
        def __call__(self): raise RuntimeError("secret_dsn_and_account")
    report=CollectionObservations(Unavailable()).report()
    assert report["collection"]["availability"]=="unavailable"
    assert "unique_games" not in report["collection"]
    assert "secret_dsn" not in json.dumps(report)


def test_public_redaction_and_output_bound():
    report=dict(schema_version=1,identity={},account="private-account",provider_game_id="private-game",
                expected_uci=["e2e4"],findings=["x"*MAX_OUTPUT_BYTES],unresolved=[])
    encoded=_encode(report)
    assert len(encoded.encode())<=MAX_OUTPUT_BYTES and json.loads(encoded)["truncated"]
    report["findings"]=[]
    encoded=_encode(report)
    assert all(value not in encoded for value in ["private-account","private-game","e2e4"])
    report["required_inputs"]={"account":False,"repertoire_ids":True}
    assert json.loads(_encode(report))["required_inputs"]=={"account":False,"repertoire_ids":True}


def test_reader_import_and_execution_have_no_command_or_provider_capabilities(stored):
    import subprocess
    import os
    script='''import sys,importlib.abc
class Refuse(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.startswith(("app.games.providers","app.command_dispatch","app.tasks","app.product_composition.game_worker","app.product_composition.game_sync_current")):
   raise AssertionError("reader imported mutation/provider capability: "+name)
sys.meta_path.insert(0,Refuse())
from app.product_composition.game_collection_status import main
raise SystemExit(main(sys.argv[1:]))
'''
    environment={**os.environ,"TEMPO_DATABASE_READ_URL":stored.database.dsn,
                 "PYTHONPATH":os.pathsep.join(sys.path)}
    environment.pop("TEMPO_DATABASE_WRITE_URL",None)
    result=subprocess.run([sys.executable,"-B","-c",script,"--workspace",stored.identity],env=environment,
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_missing_reader_never_uses_writer_or_dispatches(monkeypatch, capsys):
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "must-not-be-used")
    assert main([]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["providers"]["availability"] == "unavailable"
    assert "games_reader_profile_required" in result["unresolved"]


def _bound_payload(stage="read"):
    return dict(code="repertoire_snapshot_exceeds_bound", stage=stage, sample_complete=False,
        violations=[dict(dimension="source_rows", observed=10001, limit=10000, observation="lower_bound")])


def test_readiness_is_cached_once_per_report_and_copied_then_recomputed(stored):
    from psycopg.types.json import Jsonb
    selected = ["synthetic-healthy", "synthetic-bound", "synthetic-unknown"]
    for provider in ("lichess", "chesscom"):
        configuration = {**stored.spec, "provider": provider, "repertoire_ids": selected,
                         "speeds": ["rapid"], "rated": None, "interval_seconds": 60}
        with stored.database.admin() as database:
            database.execute_native("INSERT INTO product_feedback_configuration(workspace_id,provider,revision,next_run_at,payload) VALUES(%s,%s,1,0,%s)",
                                    (stored.identity, provider, Jsonb(configuration)))
    calls = []
    repaired = False

    def readiness(repertoire):
        calls.append(repertoire)
        if repertoire == selected[2] and not repaired:
            raise ValueError("repertoire_snapshot_exceeds_bound")
        if repertoire == selected[1] and not repaired:
            return dict(availability="unavailable", diagnostic="repertoire_snapshot_exceeds_bound", bound=_bound_payload())
        return dict(availability="available", learner_color="white")

    stored.reader.repertoire_readiness = readiness
    first = stored.reader.report(workspace=stored.identity)
    assert calls == selected
    left, right = first["providers"]["items"]
    assert left["selected_repertoire_count"] == right["selected_repertoire_count"] == 3
    assert left["repertoires_truncated"] is right["repertoires_truncated"] is False
    assert left["repertoire_readiness"] == right["repertoire_readiness"]
    assert left["repertoire_readiness"][2]["diagnostic"] == "repertoire_read_unavailable"
    left["repertoire_readiness"][1]["bound"]["violations"][0]["observed"] = 99999
    assert right["repertoire_readiness"][1]["bound"]["violations"][0]["observed"] == 10001
    repaired = True
    second = stored.reader.report(workspace=stored.identity)
    assert calls == selected * 2
    assert all(r["availability"] == "available" for p in second["providers"]["items"] for r in p["repertoire_readiness"])
    repaired = False
    third = stored.reader.report(workspace=stored.identity)
    assert calls == selected * 3
    assert third["providers"]["items"][0]["repertoire_readiness"][1]["availability"] == "unavailable"


@pytest.mark.parametrize("stage", ["read", "projection"])
def test_owner_typed_bound_from_read_or_projection_preserves_numeric_diagnostic(monkeypatch, stage):
    from contextlib import contextmanager
    from app.product_composition.game_collection_status import _repertoire_readiness
    from app.repertoire import RepertoireSnapshotBoundExceeded
    import app.repertoire as repertoire
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot
    error = RepertoireSnapshotBoundExceeded(stage=stage, sample_complete=False,
        violations=_bound_payload()["violations"])
    entered = False

    @contextmanager
    def connections():
        nonlocal entered
        entered = True
        try:
            yield object()
        finally:
            entered = False

    def read(*args):
        if stage == "read":
            raise error
        return dict(repertoire=dict(name="private-name", graph_state="ready", graph_generation=7), lines=[], graphs=[])

    def project(*args):
        assert not entered
        raise error

    monkeypatch.setattr(product_snapshot, "read_repertoire", read)
    monkeypatch.setattr(repertoire, "project_published_repertoire", project)
    result = _repertoire_readiness(connections, "private-id")
    assert result["availability"] == "unavailable"
    assert result["diagnostic"] == "repertoire_snapshot_exceeds_bound"
    assert result["bound"] == _bound_payload(stage)
    if stage == "projection":
        assert result["publication_state"] == "ready" and result["publication_revision"] == 7
    assert all(private not in _encode(result) for private in ("private-id", "private-name"))
    # The same text on an arbitrary ValueError never gains the typed permission.
    def unknown(*args):
        raise ValueError("repertoire_snapshot_exceeds_bound")
    monkeypatch.setattr(product_snapshot, "read_repertoire", unknown)
    assert _repertoire_readiness(connections, "private-id") == dict(
        availability="unavailable", diagnostic="repertoire_read_unavailable")


def test_public_bound_redaction_preserves_only_valid_numeric_owner_contract():
    payload = _bound_payload()
    payload["private"] = "private-pgn"
    payload["violations"][0]["private"] = "private-account"
    encoded = _encode(dict(diagnostic="repertoire_snapshot_exceeds_bound", bound=payload, repertoire_id="private-id"))
    assert all(private not in encoded for private in ("private-pgn", "private-account", "private-id"))
    result = json.loads(encoded)
    assert result["bound"] == _bound_payload()
    assert result["diagnostic"] == "repertoire_snapshot_exceeds_bound"
    for field, value in (("violations", []), ("stage", "private-error"), ("stage", []), ("sample_complete", "false")):
        bad = {**payload, field: value}
        assert json.loads(_encode(dict(bound=bad)))["bound"] is None
    for field, value in (("observed", True), ("observed", 10000), ("limit", -1),
                         ("dimension", []), ("observation", []), ("observed", float("inf"))):
        bad = _bound_payload()
        bad["violations"][0][field] = value
        assert json.loads(_encode(dict(bound=bad)))["bound"] is None


def test_identity_only_calls_only_identity_and_emits_complete_envelope(capsys):
    class IdentityOnly:
        def __getattr__(self, name):
            raise AssertionError("identity-only touched " + name)

        def identity(self):
            return dict(availability="available", reader_role="fixture_reader", transaction_read_only="on", schema_version=987)

    assert main(["--identity-only"], reader_factory=IdentityOnly) == 0
    raw = capsys.readouterr().out
    assert raw.endswith("\n") and raw.count("\n") == 1
    assert json.loads(raw) == dict(schema_version=1, read_only=True, identity=IdentityOnly().identity())


@pytest.mark.parametrize("selectors", [["--provider", "lichess"], ["--game-id", "synthetic"]])
def test_identity_only_rejects_game_and_provider_selectors(selectors):
    with pytest.raises(SystemExit) as error:
        main(["--identity-only", *selectors], reader_factory=lambda: pytest.fail("reader must not be constructed"))
    assert error.value.code == 2


def test_identity_only_missing_reader_credentials_never_uses_writer(monkeypatch, capsys):
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "must-not-be-used")
    assert main(["--identity-only"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == dict(schema_version=1, read_only=True,
        identity=dict(availability="unavailable", diagnostic="games_reader_profile_required"))


def test_identity_only_native_reader_does_not_touch_other_ports(stored, monkeypatch, capsys):
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", stored.database.dsn)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "must-not-be-used")
    def forbidden(*args, **kwargs):
        pytest.fail("identity-only touched a full-report section")
    for name in ("report", "providers", "collection", "delivery", "selected_game"):
        monkeypatch.setattr(CollectionObservations, name, forbidden)
    assert main(["--identity-only"]) == 0
    identity = json.loads(capsys.readouterr().out)["identity"]
    assert identity["availability"] == "available"
    assert identity["reader_role"] == stored.database.role
    assert identity["transaction_read_only"] == "on"


def test_identity_only_unavailable_identity_exits_two(capsys):
    reader = SimpleNamespace(identity=lambda: dict(availability="unavailable", diagnostic="collection_read_unavailable"))
    assert main(["--identity-only"], reader_factory=lambda: reader) == 2
    assert json.loads(capsys.readouterr().out)["identity"]["availability"] == "unavailable"


def test_status_distinguishes_priority_pending_actual_application_queue_and_fresh_issuance():
    from app.contracts.product_v02.learning import EncounterReceipt
    from tempo_legacy_database.adapters.games.collection_observations import study_receipt_progress
    fixtures = ROOT / "contracts/product-v02/fixtures"
    def receipt(folder, name):
        return EncounterReceipt.model_validate_json(json.dumps(json.loads((fixtures / folder / (name+".json")).read_text())["payload"]))
    legacy = receipt("encounters", "historical-review-priority")
    old = study_receipt_progress(legacy)
    assert old["stage"] == "legacy_priority_needs_qualification" and not old["again"]["observed"]
    pending = receipt("first-departure", "protected-target-pending-reset")
    facts = study_receipt_progress(pending, dict(progress=pending.progress, target_compatible=True,
        queued_attempt=dict(queue_entry_id=1, queue_date="2026-10-30", status="queued", attempt_state="not_issued",
            card_id=pending.progress.target.card_id, card_revision=1, issuance=None), issued_since_determination=6))
    assert facts["stage"] == "accepted_pending" and facts["queue"]["entry"] is not None
    assert not facts["introduction"]["observed"] and not facts["issuance"]["observed"]
    assert facts["issuance"]["issued_since_determination"] == 6
    remapped = receipt("first-departure", "remapped-again-preserves-first-service")
    facts = study_receipt_progress(remapped)
    assert facts["stage"] == "settled" and facts["again"]["review_id"] == 41
    assert facts["application_target"]["card_id"] == "existing-physical-card"
    assert facts["current_target"]["card_id"] == "current-composite-b"
    assert facts["issuance"]["ordinal"] == 1 and facts["issuance"]["current_remediation_issued"]
    public = _encode(dict(identity={}, selected_game=dict(progress=facts)))
    assert "existing-physical-card" not in public and "current-composite-b" not in public
    assert json.loads(public)["selected_game"]["progress"]["again"]["review_id"] == 41
