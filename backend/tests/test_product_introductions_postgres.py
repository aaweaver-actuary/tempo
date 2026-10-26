"""T14 real transaction contract on one capped, labelled disposable PostgreSQL.

Uses an existing postgres:18.6-trixie image only. No pull/install/external DSN,
live data or service operation. Small SQL fixture owner exercises the application
port and existing PostgresConnection; normal-product wiring remains #501-owned.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import secrets
import subprocess
from threading import Barrier
import time
import uuid

import psycopg
import pytest

from app.study.introductions.application import Introductions
from app.study.introductions.ports import OwnerFacts
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.study.product_introductions import PostgresIntroductionStore, SCHEMA_SQL
from test_product_introductions import CONTEXT, DAY, NEXT_DAY, candidate, ranking

ROOT = Path(__file__).resolve().parents[2]


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check, timeout=20)


@pytest.fixture(scope="module")
def owned_postgres(tmp_path_factory):
    identity = "tempo-introductions-" + uuid.uuid4().hex
    container = identity + "-postgres"
    report = {"owner": identity, "container": container, "cleanup_verified": False}
    evidence = ROOT / "test-results/product-introductions-native" / identity
    evidence.mkdir(parents=True)
    password = secrets.token_urlsafe(32)
    secret = tmp_path_factory.mktemp("introductions-secret") / "postgres.env"
    secret.write_text("POSTGRES_PASSWORD=" + password + "\nPOSTGRES_DB=synthetic_introductions\n")
    secret.chmod(0o600)
    try:
        image = docker("image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}").stdout.strip()
        report["image_id"] = image
        report["container_id"] = docker("run", "--detach", "--name", container,
            "--label", "tempo.owner=" + identity, "--label", "tempo.disposable=true",
            "--memory", "384m", "--memory-swap", "384m", "--cpus", "1", "--pids-limit", "128",
            "--tmpfs", "/var/lib/postgresql:rw,size=512m", "--publish", "127.0.0.1::5432",
            "--env-file", str(secret), image, "postgres", "-c", "shared_buffers=32MB",
            "-c", "max_connections=16").stdout.strip()
        current = json.loads(docker("inspect", container).stdout)[0]
        assert current["HostConfig"]["Tmpfs"] == {"/var/lib/postgresql": "rw,size=512m"}
        assert current["HostConfig"]["Memory"] == 384 * 1024 * 1024
        assert current["HostConfig"]["PidsLimit"] == 128
        assert all(mount["Type"] == "tmpfs" for mount in current["Mounts"])
        binding = current["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        options = dict(host="127.0.0.1", port=int(binding["HostPort"]), user="postgres",
                       dbname="synthetic_introductions", password=password,
                       sslmode="disable", connect_timeout=2)
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(**options) as probe:
                    assert probe.execute("SELECT current_database()").fetchone()[0] == options["dbname"]
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned PostgreSQL fixture did not become ready")
                docker("exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-t", "1", check=False)
        report["loopback_port"] = int(binding["HostPort"])
        yield options
    finally:
        found = docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        if found:
            current = json.loads(docker("inspect", container).stdout)[0]
            assert current["Config"]["Labels"]["tempo.owner"] == identity
            assert current["Config"]["Labels"]["tempo.disposable"] == "true"
            logs = docker("logs", container, check=False)
            (evidence / "postgres.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", current["Id"])
        assert not docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        report["cleanup_verified"] = True
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))
        secret.unlink(missing_ok=True)


@pytest.fixture
def transaction(owned_postgres):
    schema = "case_" + uuid.uuid4().hex
    with psycopg.connect(**owned_postgres) as raw:
        raw.execute(f'CREATE SCHEMA "{schema}"')  # task-generated identifier only
        raw.execute(f'SET search_path TO "{schema}"')
        raw.execute(SCHEMA_SQL, prepare=False)
        raw.execute("""CREATE TABLE fixture_introduced(
            presentation_id TEXT PRIMARY KEY, study_day TEXT NOT NULL, repertoire_id TEXT NOT NULL);
            CREATE TABLE fixture_queue(presentation_id TEXT PRIMARY KEY);
            CREATE TABLE fixture_active(attempt TEXT PRIMARY KEY);
            INSERT INTO fixture_active VALUES('unrelated-issued-attempt');""", prepare=False)

    @contextmanager
    def open_transaction():
        with psycopg.connect(**owned_postgres, row_factory=tempo_row_factory) as raw:
            raw.execute(f'SET LOCAL search_path TO "{schema}"')
            raw.execute("SET LOCAL lock_timeout='3s'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)

    yield open_transaction
    with psycopg.connect(**owned_postgres) as raw:
        raw.execute(f'DROP SCHEMA "{schema}" CASCADE')


class SqlFixtureOwner:
    """Only task-owned synthetic tables; this is a typed port contract fixture."""
    def __init__(self, database, *, context=CONTEXT, budget=1):
        self.database, self.context, self.budget = database, context, budget

    def facts(self, day):
        rows = self.database.execute("SELECT presentation_id,study_day,repertoire_id FROM fixture_introduced", ()).fetchall()
        return OwnerFacts(self.context, (candidate("a1"), candidate("a2"), candidate("b1", "b"), candidate("b2", "b")),
                          self.budget, (("a", 1), ("b", 1)),
                          tuple((row[0], row[2]) for row in rows if row[1] == day),
                          frozenset(row[0] for row in rows), enabled=True)

    def introduce(self, day, choice):
        if self.database.execute("SELECT 1 FROM fixture_introduced WHERE presentation_id=?", (choice.presentation_id,)).fetchone():
            return "already_introduced"
        self.database.execute("INSERT INTO fixture_introduced VALUES(?,?,?)", (choice.presentation_id, day, choice.repertoire_id))
        self.database.execute("INSERT INTO fixture_queue VALUES(?)", (choice.presentation_id,))
        return "introduced"


def service(database, **changes):
    return Introductions(PostgresIntroductionStore(database), SqlFixtureOwner(database, **changes))


def test_postgres_atomic_introduction_budget_and_fairness_rollback(transaction):
    class FailingStore(PostgresIntroductionStore):
        def record_introduction(self, receipt, *, period_rotation_after):
            super().record_introduction(receipt, period_rotation_after=period_rotation_after)
            raise RuntimeError("synthetic failure after all effects")

    with pytest.raises(RuntimeError, match="after all effects"):
        with transaction() as database:
            app = Introductions(FailingStore(database), SqlFixtureOwner(database))
            choice = app.preview(DAY).introductions[0]
            app.commit(DAY, choice, CONTEXT)
    with transaction() as database:
        app = service(database)
        assert not app.store.state(DAY).charges and not app.store.state(DAY).period_started
        assert app.store.state(DAY).rotation_after is None
        assert database.execute("SELECT COUNT(*) FROM fixture_queue", ()).fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM fixture_introduced", ()).fetchone()[0] == 0
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"


def test_postgres_concurrent_admission_and_retry_charge_once(transaction):
    barrier = Barrier(2)
    with transaction() as database:
        choice = service(database).preview(DAY).introductions[0]

    def attempt():
        with transaction() as database:
            app = service(database)
            barrier.wait(timeout=5)
            return app.commit(DAY, choice, CONTEXT).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["introduced", "replayed"]
    with transaction() as database:
        app = service(database)
        assert len(app.store.state(DAY).charges) == 1
        assert database.execute("SELECT COUNT(*) FROM fixture_queue", ()).fetchone()[0] == 1
        assert app.commit(NEXT_DAY, choice, CONTEXT).status == "replayed"


def test_postgres_multiday_fairness_rebuild_and_persisted_reload(transaction):
    for day, expected in ((DAY, "a"), (NEXT_DAY, "b")):
        for _ in range(4):
            with transaction() as database:
                app = service(database)
                before = app.store.state(day)
                assert app.preview(day).introductions[0].repertoire_id == expected
                assert app.store.state(day) == before
        with transaction() as database:
            app = service(database)
            choice = app.preview(day).introductions[0]
            assert app.commit(day, choice, CONTEXT).status == "introduced"
        for _ in range(3):
            with transaction() as database:
                app = service(database)
                before = app.store.state(day)
                assert app.commit(day, choice, CONTEXT).status == "replayed"
                assert not app.preview(day).introductions
                assert app.store.state(day) == before
    with transaction() as database:
        rows = database.execute("SELECT study_day,repertoire_id FROM study_product_introduction_charges ORDER BY study_day", ()).fetchall()
        assert [tuple(row) for row in rows] == [(DAY, "a"), (NEXT_DAY, "b")]
        assert database.execute("SELECT COUNT(*) FROM fixture_active WHERE attempt='unrelated-issued-attempt'", ()).fetchone()[0] == 1


def test_postgres_saved_ranking_offline_settings_fence_and_active_attempt(transaction):
    value = ranking(("a2", "a", .2), ("a1", "a", .02))
    with transaction() as database:
        assert service(database).accept_ranking(DAY, CONTEXT, value) == "accepted"
    with transaction() as database:
        app = service(database)
        assert app.preview(DAY).introductions[0].decision_id == "a2"
        assert app.accept_ranking(DAY, CONTEXT, value.model_copy(update={"unavailable_reason": "offline"})) == "rejected"
    with transaction() as database:
        assert service(database).preview(DAY).introductions[0].decision_id == "a2"
        changed = replace(CONTEXT, settings_revision="explicit-settings-2")
        assert service(database, context=changed).preview(DAY).introductions[0].decision_id == "a1"
        assert service(database, context=changed).accept_ranking(DAY, CONTEXT, value) == "rejected"
        assert not PostgresIntroductionStore(database).state(DAY).charges
        assert database.execute("SELECT COUNT(*) FROM fixture_active", ()).fetchone()[0] == 1


def test_postgres_existing_owner_facts_and_ledger_are_not_double_counted(transaction):
    with transaction() as database:
        app = service(database, budget=2)
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"
    with transaction() as database:
        app = service(database, budget=2)
        assert len(app.preview(DAY).introductions) == 1
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"
    with transaction() as database:
        assert not service(database, budget=2).preview(DAY).introductions
        assert len(service(database, budget=2).store.state(DAY).charges) == 2


def test_postgres_invalid_optional_cache_preserves_safe_fallback(transaction):
    with transaction() as database:
        assert service(database).accept_ranking(DAY, CONTEXT, ranking(("a2", "a", .2))) == "accepted"
        database.execute("UPDATE study_product_introduction_rankings SET ranking_json=?", ("obsolete-json",))
    with transaction() as database:
        assert service(database).preview(DAY).introductions[0].decision_id == "a1"
        assert not service(database).store.state(DAY).charges


@pytest.fixture(scope='module')
def legacy_transaction(owned_postgres):
    """Actual source schema, isolated from the six unchanged private contracts."""
    import importlib.util
    import hashlib
    from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
    spec = importlib.util.spec_from_file_location('introductions_migrations', ROOT / 'database/tooling/apply_postgres_migrations.py')
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)
    files = sorted(migrations.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
    migrations.validate_migration_history(files, [], expected_version=POSTGRES_SCHEMA_VERSION)
    with psycopg.connect(**owned_postgres) as raw:
        for path in files:
            registration = migrations.MODULE_MIGRATIONS.get(path.name)
            if registration:
                source = (ROOT / 'database' / registration[2]).read_bytes()
                assert hashlib.sha256(source).hexdigest() == registration[3]
                raw.execute(source.decode(), prepare=False)
            raw.execute(path.read_text(), prepare=False)
        raw.execute(SCHEMA_SQL, prepare=False)
        raw.execute('INSERT INTO settings(id) VALUES(1)')

    @contextmanager
    def open_transaction():
        with psycopg.connect(**owned_postgres, row_factory=tempo_row_factory) as raw:
            raw.execute("SET LOCAL lock_timeout='3s'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)
    return open_transaction


@pytest.fixture
def current_owner_case(legacy_transaction):
    import chess
    from tempo_evidence_math import opening_position_key
    from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken, decision_identity, fingerprint
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    from app.study.introductions.ports import IntroductionSettings, IntroductionSource
    from tempo_legacy_database.adapters.queue_refresh.application.admission import product_source_facts
    identity = uuid.uuid4().hex
    repertoire, line, card = ('rep-' + identity, 'line-' + identity, 'card-' + identity)
    board = chess.Board()
    board.push_uci('e2e4')
    board.push_uci('e7e5')
    start = board.fen()
    with legacy_transaction() as database:
        database.execute('UPDATE settings SET study_only_mode=0,new_cards_per_day=10 WHERE id=1', ())
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (repertoire,'Synthetic','fixture',DAY))
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,'white',?,?,?)",
                         (line,repertoire,line,chess.STARTING_FEN,json.dumps(['e2e4','e7e5','g1f3']),DAY))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,?,'response',?,?,'locked',?,'opening','white')",
                         (card,repertoire,start,json.dumps(['g1f3']),DAY))
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (repertoire,card))
        for extra in (card + '-due', card + '-active'):
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color,introduced_at) VALUES(?,?,'response',?,?,'learning',?,'opening','white','2026-10-25')",
                             (extra,repertoire,start,json.dumps(['g1f3']),DAY))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,attempt_state) VALUES(?,?,0,'in_progress')", (DAY,card + '-active'))
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,?,1,?,NULL,?,?,?,'white')",
                         (repertoire,line,card,opening_position_key(start),start,json.dumps(['g1f3'])))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(?,1,'ready',?)", (repertoire,DAY))
        header, _, _, _ = product_source_facts(database,repertoire,[card],[line])
        token = LegacyAuthorityToken(kind='legacy_graph',repertoire_id=repertoire,
            source_digest='a'*64,catalogue_digest='b'*64,graph_digest='c'*64,
            **{key:header[key] for key in header.keys() if key in LegacyAuthorityToken.model_fields})
        snapshot = json.loads((ROOT / 'contracts/product-v02/fixtures/snapshot.json').read_text())['payload']
        snapshot['membership'].update(workspace_id=token.workspace_id,revision=1,
            source_revision=fingerprint(token.model_dump(mode='json')),scope_repertoire_ids=[repertoire],scope_id=repertoire)
        snapshot['membership']['routes'] = [dict(repertoire_id=repertoire,route_id=line,policy_id=repertoire,
            learner_color='white',starting_fen=chess.STARTING_FEN,moves=['e2e4','e7e5','g1f3'])]
        snapshot['boundaries'] = [dict(route_id=line,terminal_ply=3,deliberate=True)]
        snapshot['decision_presentations'] = [dict(decision_id=decision_identity(card,0,[]), repertoire_id=repertoire,
            position_key=opening_position_key(start),selected_response_uci='g1f3',presentation_id=card,
            presentation_revision=1,repertoire_revision=1,introduction='unintroduced',introduced_at=None,
            available=True,introduction_matches_current_content=True)]
        source = IntroductionSource(RepertoireSnapshot.model_validate_json(json.dumps(snapshot)),token)
    yield legacy_transaction, source, IntroductionSettings('synthetic-explicit-settings',1,((repertoire,1),),enabled=True), card
    with legacy_transaction() as database:
        database.execute('DELETE FROM study_product_introduction_charges WHERE repertoire_id=?',(repertoire,))
        for identity in (card,card + '-due',card + '-active',card + '-root'):
            database.execute('DELETE FROM daily_queue WHERE card_id=?',(identity,))
            database.execute('DELETE FROM cards WHERE id=?',(identity,))
        database.execute('DELETE FROM repertoires WHERE id=?',(repertoire,))
        database.execute('UPDATE settings SET study_only_mode=0,new_cards_per_day=10 WHERE id=1',())


def test_current_owner_deep_introduction_uses_actual_canonical_writer(current_owner_case):
    transaction, source, settings, card = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        active = tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card + '-active',)).fetchone())
        assert app.preview(DAY).introductions[0].presentation_id == card
        assert app.admit_next(DAY).status == 'introduced'
        assert tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card + '-active',)).fetchone()) == active
        positions = database.execute('SELECT card_id,position FROM daily_queue WHERE card_id=ANY(?) ORDER BY position',([card,card + '-due'],)).fetchall()
        assert [row[0] for row in positions] == [card + '-due',card]
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        assert not app.preview(DAY).introductions
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=?',(card,)).fetchone()) == ('learning',DAY)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE queue_date=? AND card_id=?',(DAY,card)).fetchone()[0] == 1
        assert app.store.receipt(card).repertoire_id == source.authority.repertoire_id
        assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(card,)).fetchone()[0] == 0


@pytest.mark.parametrize('change',['source','definition','study_only','blocked','membership','illegal_route','legacy_global_pause','legacy_repertoire_pause'])
def test_current_owner_fences_changed_eligibility_and_saved_controls(current_owner_case,change):
    transaction, source, settings, card = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        choice, context = app.preview(DAY).introductions[0], app.context(DAY)
    with transaction() as database:
        if change == 'source':
            database.execute('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=?',(source.authority.repertoire_id,))
        elif change == 'definition': database.execute('UPDATE cards SET revision=revision+1 WHERE id=?',(card,))
        elif change == 'study_only': database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1',())
        elif change == 'legacy_global_pause': database.execute('UPDATE settings SET new_cards_per_day=0 WHERE id=1',())
        elif change == 'legacy_repertoire_pause': database.execute('UPDATE repertoires SET new_cards_per_day=0 WHERE id=?',(source.authority.repertoire_id,))
        elif change == 'blocked': database.execute('UPDATE cards SET pending_validation=1 WHERE id=?',(card,))
        elif change == 'membership':
            database.execute('DELETE FROM repertoire_cards WHERE card_id=?',(card,))
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__retained_cards__','Retained','fixture',?) ON CONFLICT(id) DO NOTHING",(DAY,))
            database.execute("UPDATE cards SET repertoire_id='__retained_cards__' WHERE id=?",(card,))
        else: database.execute("UPDATE repertoire_lines SET moves_json='[\"e2e5\"]' WHERE id=?",(source.snapshot.membership.routes[0].route_id,))
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        assert app.commit(DAY,choice,context).status == 'stale'
        assert app.store.receipt(card) is None
        assert not database.execute('SELECT 1 FROM daily_queue WHERE card_id=?',(card,)).fetchone()


def test_current_owner_atomic_retry_and_rollback(current_owner_case):
    transaction, source, settings, card = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    with pytest.raises(RuntimeError,match='rollback after canonical write'):
        with transaction() as database:
            app = current_owner_introductions(database,sources=(source,),settings=settings)
            assert app.admit_next(DAY).status == 'introduced'
            raise RuntimeError('rollback after canonical write')
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        choice, context = app.preview(DAY).introductions[0], app.context(DAY)
        assert app.store.receipt(card) is None
    barrier = Barrier(2)
    def commit():
        with transaction() as database:
            app = current_owner_introductions(database,sources=(source,),settings=settings)
            barrier.wait(timeout=5)
            return app.commit(DAY,choice,context).status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _:commit(),range(2)))
        assert outcomes.count('introduced') == 1
        assert set(outcomes) <= {'introduced','replayed','deferred'}
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        assert app.commit(DAY,choice,context).status == 'replayed'
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == 1


def test_current_owner_seed_queue_explicit_hook_preserves_charge_and_issued_row(current_owner_case):
    transaction, source, settings, card = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    from app.services.queue_application import seed_queue
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=replace(settings,enabled=False))
        seed_queue(database,DAY,introductions=app)
        assert app.store.receipt(card) is None
    with transaction() as database:
        active = tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card + '-active',)).fetchone())
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        seed_queue(database,DAY,introductions=app)
        seed_queue(database,DAY,introductions=app)
        assert tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card + '-active',)).fetchone()) == active
        assert app.store.receipt(card) is not None
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=?',(card,)).fetchone()) == ('learning',DAY)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == 1


def test_current_owner_source_fence_holds_through_canonical_write(current_owner_case):
    transaction, source, settings, card = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    line = source.snapshot.membership.routes[0].route_id
    def edit():
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with transaction() as database:
                database.execute_native("SET LOCAL lock_timeout='100ms'",())
                database.execute('UPDATE repertoire_lines SET moves_json=moves_json WHERE id=?',(line,))
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        assert app.admit_next(DAY).status == 'introduced'
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(edit).result(timeout=5)
    with transaction() as database:
        database.execute('UPDATE repertoire_lines SET moves_json=moves_json WHERE id=?',(line,))
        assert PostgresIntroductionStore(database).receipt(card) is not None


def test_current_owner_seed_queue_default_keeps_legacy_writer(current_owner_case):
    transaction, _, _, card = current_owner_case
    from app.services.queue_application import seed_queue
    with transaction() as database:
        seed_queue(database,DAY)
        assert PostgresIntroductionStore(database).receipt(card) is None
        assert tuple(database.execute('SELECT state,introduced_at FROM cards WHERE id=?',(card,)).fetchone()) == ('learning',DAY)
        assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?',(card,)).fetchone()[0] == 1


def test_current_owner_unchanged_content_retains_ranking_across_capture_refresh(current_owner_case):
    transaction, source, settings, _ = current_owner_case
    from app.services.queue_admission_adapter import current_owner_introductions
    mapping = source.snapshot.decision_presentations[0]
    value = ranking((mapping.decision_id,source.authority.repertoire_id,.2))
    value = value.model_copy(update={'based_on':value.based_on.model_copy(update={
        'repertoire_revisions':((source.authority.repertoire_id,1),)})})
    with transaction() as database:
        app = current_owner_introductions(database,sources=(source,),settings=settings)
        context = app.context(DAY)
        assert app.accept_ranking(DAY,context,value) == 'accepted'
    refreshed = replace(source,snapshot=source.snapshot.model_copy(update={'membership':
        source.snapshot.membership.model_copy(update={'digest':'d'*64,'snapshot_id':'refreshed-capture'})}))
    with transaction() as database:
        app = current_owner_introductions(database,sources=(refreshed,),settings=settings)
        assert app.context(DAY) == context
        assert app.store.saved_ranking(context) == value
        assert not app.store.state(DAY).charges


@pytest.fixture(scope='module')
def repertoire_bridge_schema(legacy_transaction):
    """Published conflict-owner DDL, required only for the changed bridge proof.

    The leaf's source qualification assembly contains unchanged #483/#487
    dependency files. Integrated suites use those same normal source imports.
    No runtime source fallback, downloaded module or duplicate policy is used.
    """
    from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import current_snapshot
    from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore
    with legacy_transaction() as database:
        database.raw.execute((ROOT/'database/tempo_legacy_database/ddl/study_conflicts.sql').read_text(),prepare=False)
    return current_snapshot, PostgresConflictStore


def repertoire_bridge(database, repertoire_id, settings, *, source=None, conflicts=None):
    from app.services.queue_admission_adapter import current_owner_introductions
    from app.study.introductions.ports import RepertoireIntroductionSource
    from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import current_snapshot
    class Snapshots:
        def snapshot(self, identity): return current_snapshot(database,identity)
    snapshots = Snapshots()
    source = source or RepertoireIntroductionSource(snapshots.snapshot(repertoire_id))
    receiver = current_owner_introductions(database,sources=(source,),settings=settings,
        current_snapshots=snapshots,conflicts=conflicts)
    return receiver,source


def add_repertoire_bridge_root(database, repertoire_id, child_id):
    import chess
    from tempo_evidence_math import opening_position_key
    root = child_id + '-root'
    line = database.execute('SELECT line_id FROM opening_graph_steps WHERE card_id=?',(child_id,)).fetchone()[0]
    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(?,?,'prefix',?,'[\"e2e4\"]','locked',?,'opening','white')",
                     (root,repertoire_id,chess.STARTING_FEN,DAY))
    database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)',(repertoire_id,root))
    database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,?,0,?,?,?,'[\"e2e4\"]','white')",
                     (repertoire_id,line,root,opening_position_key(chess.STARTING_FEN),chess.STARTING_FEN))
    return root


def test_repertoire_bridge_encounter_priority_without_game_provider_or_ranking(current_owner_case,repertoire_bridge_schema,monkeypatch):
    transaction, retained, settings, card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    with transaction() as database:
        root = add_repertoire_bridge_root(database,repertoire_id,card)
        receiver,source = repertoire_bridge(database,repertoire_id,settings)
        assert not hasattr(source,'authority')
        assert database.execute('SELECT COUNT(*) FROM imported_games',()).fetchone()[0] == 0
        assert receiver.store.saved_ranking(receiver.context(DAY)) is None
        first = receiver.preview(DAY).introductions[0].presentation_id
        target = root if first == card else card
        decision = next(item.decision_id for item in source.snapshot.decision_presentations if item.presentation_id == target)
        active = tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card+'-active',)).fetchone())
        import builtins
        original_import = builtins.__import__
        def independent(name,*args,**kwargs):
            if name == 'app.games' or name.startswith(('app.games.','app.analysis.')):
                raise AssertionError('optional implementation imported during introduction: '+name)
            return original_import(name,*args,**kwargs)
        with monkeypatch.context() as guarded:
            guarded.setattr(builtins,'__import__',independent)
            assert receiver.admit_encounter(DAY,repertoire_id=repertoire_id,decision_id=decision).status == 'introduced'
        assert receiver.admit_encounter(DAY,repertoire_id=repertoire_id,decision_id=decision).status == 'replayed'
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(first,)).fetchone()[0] is None
        assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(target,)).fetchone()[0] == 0
        assert len(receiver.store.state(DAY).charges) == 1
        assert tuple(database.execute('SELECT * FROM daily_queue WHERE card_id=?',(card+'-active',)).fetchone()) == active
        positions = database.execute('SELECT card_id FROM daily_queue WHERE card_id=ANY(?) ORDER BY position',([target,card+'-due'],)).fetchall()
        assert [row[0] for row in positions] == [card+'-due',target]


def test_repertoire_bridge_refreshes_only_own_fenced_introduction_changes(current_owner_case,repertoire_bridge_schema):
    transaction, retained, settings, card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    with transaction() as database:
        add_repertoire_bridge_root(database,repertoire_id,card)
        receiver,source = repertoire_bridge(database,repertoire_id,replace(settings,global_budget=2))
        context = receiver.context(DAY)
        assert len(receiver.admit(DAY,maximum_new=2)) == 2
        assert receiver.owner.sources[0].snapshot.membership.source_revision != source.snapshot.membership.source_revision
        assert receiver.context(DAY) == context
    with transaction() as database:
        stale,_ = repertoire_bridge(database,repertoire_id,replace(settings,global_budget=2),source=source)
        assert not stale.preview(DAY).introductions and stale.owner.diagnostics
        fresh,_ = repertoire_bridge(database,repertoire_id,replace(settings,global_budget=2))
        assert fresh.context(DAY) == context
        assert len(fresh.store.state(DAY).charges) == 2


def test_repertoire_bridge_real_scoped_conflict_hold_never_charges(current_owner_case,repertoire_bridge_schema):
    transaction,retained,settings,card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    _,Store = repertoire_bridge_schema
    from app.study.conflicts import Occurrence,SourceFence
    from datetime import datetime,timezone
    with transaction() as database:
        receiver,source = repertoire_bridge(database,repertoire_id,settings)
        member = source.snapshot.membership
        line = source.snapshot.membership.routes[0].route_id
        target = Occurrence(repertoire_id,line,2,card,1)
        fence = SourceFence(member.source_revision,member.revision,member.digest)
        store = Store(database)
        store.hold(target,fence,True,datetime.now(timezone.utc))
        receiver,_ = repertoire_bridge(database,repertoire_id,settings,conflicts=store)
        decision = next(item.decision_id for item in source.snapshot.decision_presentations if item.presentation_id == card)
        assert receiver.admit_encounter(DAY,repertoire_id=repertoire_id,decision_id=decision).status == 'deferred'
        assert receiver.store.receipt(card) is None
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?',(card,)).fetchone()[0] is None
        store.hold(target,fence,False,datetime.now(timezone.utc))
        assert receiver.admit_encounter(DAY,repertoire_id=repertoire_id,decision_id=decision).status == 'introduced'


def test_repertoire_bridge_rechecks_real_hold_after_preview(current_owner_case,repertoire_bridge_schema):
    transaction,retained,settings,card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    _,Store = repertoire_bridge_schema
    from app.study.conflicts import Occurrence,SourceFence
    from datetime import datetime,timezone
    with transaction() as database:
        receiver,source = repertoire_bridge(database,repertoire_id,settings)
        choice,context = receiver.preview(DAY).introductions[0],receiver.context(DAY)
    with transaction() as database:
        member = source.snapshot.membership
        Store(database).hold(Occurrence(repertoire_id,member.routes[0].route_id,2,card,1),
            SourceFence(member.source_revision,member.revision,member.digest),True,datetime.now(timezone.utc))
    with transaction() as database:
        receiver,_ = repertoire_bridge(database,repertoire_id,settings,source=source,conflicts=Store(database))
        assert receiver.commit(DAY,choice,context).status == 'stale'
        assert receiver.store.receipt(card) is None


@pytest.mark.parametrize('change',['route','definition','membership','study_only'])
def test_repertoire_bridge_retains_source_membership_and_control_fences(current_owner_case,repertoire_bridge_schema,change):
    transaction,retained,settings,card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    with transaction() as database:
        receiver,source = repertoire_bridge(database,repertoire_id,settings)
        choice,context = receiver.preview(DAY).introductions[0],receiver.context(DAY)
    with transaction() as database:
        if change == 'route':
            database.execute("UPDATE repertoire_lines SET moves_json='[\"d2d4\"]' WHERE repertoire_id=?",(repertoire_id,))
        elif change == 'definition': database.execute('UPDATE cards SET revision=revision+1 WHERE id=?',(card,))
        elif change == 'study_only': database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1',())
        else:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__retained_cards__','Retained','fixture',?) ON CONFLICT(id) DO NOTHING",(DAY,))
            database.execute('DELETE FROM repertoire_cards WHERE card_id=?',(card,))
            database.execute("UPDATE cards SET repertoire_id='__retained_cards__' WHERE id=?",(card,))
    with transaction() as database:
        receiver,_ = repertoire_bridge(database,repertoire_id,settings,source=source)
        assert receiver.commit(DAY,choice,context).status == 'stale'
        assert receiver.store.receipt(card) is None


def test_repertoire_bridge_content_cache_is_not_a_stale_source_grant(current_owner_case,repertoire_bridge_schema):
    transaction,retained,settings,card = current_owner_case
    repertoire_id = retained.snapshot.membership.scope_repertoire_ids[0]
    from app.study.introductions.application import repertoire_introduction_binding
    with transaction() as database:
        receiver,source = repertoire_bridge(database,repertoire_id,settings)
    with transaction() as database:
        database.execute("UPDATE cards SET introduced_at='2026-10-24' WHERE id=?",(card+'-due',))
    with transaction() as database:
        receiver,_ = repertoire_bridge(database,repertoire_id,settings,source=source)
        fresh = receiver.owner.current_snapshots.snapshot(repertoire_id)
        assert repertoire_introduction_binding(fresh) == repertoire_introduction_binding(source.snapshot)
        assert fresh.membership.source_revision != source.snapshot.membership.source_revision
        assert not receiver.preview(DAY).introductions
        assert receiver.owner.diagnostics
        assert receiver.store.receipt(card) is None
