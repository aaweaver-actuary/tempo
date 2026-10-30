"""Real T06 transaction contract; creates only its own capped PostgreSQL fixture.

Run: make python-file FILE=backend/tests/test_product_game_lapse_postgres.py
Requires existing Docker daemon/image postgres:18.6-trixie and backend Python
dependencies. No install/pull/restart, external DSN, existing DB or live settings.
One unique labelled container, unrestricted CPU/384MiB/128 PIDs, 512MiB tmpfs, loopback-only
ephemeral port; startup <=45s, command <=180s in the outer validation window.
The fixture applies checked-in source DDL in the owner's ordered/pinned registry,
retains nonsecret identity/cleanup evidence in test-results/game-feedback-native,
and verifies exact owner labels before removing its container. Teardown failure
fails the run. No sleeps or wall-clock scheduling assertions; policy time frozen.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from datetime import timedelta
import importlib.util
from itertools import count
import json
import hashlib
from pathlib import Path
import secrets
import subprocess
from threading import Barrier
import time
import uuid

import psycopg
import pytest

from app.contracts.product_v02.learning import GameEffectControl
from app.study.game_feedback import GameFeedback, effect_id
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore, resume_available_feedback, settle_saved_review, invalidate_saved_review
from test_product_game_lapse import (CARD, NOW, Decisions, MemoryStore, fixture,
    accept_and_refresh, qualification_for)
from app.contracts.product_v02.learning import GameLapse

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:18.6-trixie"
RETAINED_POSITION_SEQUENCE = count(1)


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=20)


@pytest.fixture(scope="module")
def owned_postgres(tmp_path_factory, *, additional_ports=()):
    # The packaged browser journey reuses this exact owned network namespace;
    # only its existing web service may request an extra loopback-only port.
    assert additional_ports in ((),(80,))
    published = [value for port in additional_ports for value in ('--publish',f'127.0.0.1::{port}')]
    identity = "tempo-game-feedback-" + uuid.uuid4().hex
    container = identity + "-postgres"
    password = secrets.token_urlsafe(32)
    evidence = ROOT / "test-results/game-feedback-native" / identity
    evidence.mkdir(parents=True)
    report = {"owner": identity, "container": container, "cleanup_verified": False}
    secret = tmp_path_factory.mktemp("game-feedback-secret") / "postgres.env"
    secret.write_text("POSTGRES_PASSWORD=" + password + "\nPOSTGRES_DB=" + identity.replace("-", "_") + "\n")
    secret.chmod(0o600)
    database_name = identity.replace("-", "_")
    try:
        image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}").stdout.strip()
        report["image_id"] = image
        report["container_id"] = docker("run", "--detach", "--name", container,
            "--label", "tempo.owner=" + identity, "--label", "tempo.disposable=true",
            "--memory", "384m", "--memory-swap", "384m", "--pids-limit", "128",
            "--tmpfs", "/var/lib/postgresql:rw,size=512m", "--publish", "127.0.0.1::5432", *published,
            "--env-file", str(secret), image, "postgres", "-c", "shared_buffers=32MB",
            "-c", "max_connections=16").stdout.strip()
        inventory = json.loads(docker("inspect", container).stdout)[0]
        report["mounts"] = inventory["Mounts"]
        report["tmpfs"] = inventory["HostConfig"]["Tmpfs"]
        report["memory_bytes"] = inventory["HostConfig"]["Memory"]
        report["pids_limit"] = inventory["HostConfig"]["PidsLimit"]
        report["published_ports"] = inventory["NetworkSettings"]["Ports"]
        assert inventory["HostConfig"]["Tmpfs"] == {"/var/lib/postgresql": "rw,size=512m"}
        assert report["memory_bytes"] == 384 * 1024 * 1024 and report["pids_limit"] == 128
        binding = inventory["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        assert all(mount["Type"] == "tmpfs" for mount in inventory["Mounts"])
        port = int(binding["HostPort"])
        report.update(port=port, database=database_name)
        options = dict(host="127.0.0.1", port=port, dbname=database_name, user="postgres",
                       password=password, sslmode="disable", connect_timeout=2)
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(**options) as probe:
                    assert probe.execute('SELECT current_database()').fetchone()[0] == database_name
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned PostgreSQL TCP endpoint did not become ready")
                docker("exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", database_name, "-t", "1", check=False)
        spec = importlib.util.spec_from_file_location("game_feedback_migration_owner", ROOT / "database/tooling/apply_postgres_migrations.py")
        migration_owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_owner)
        # This is a source/native contract, not installed-artifact qualification.
        # Reuse the owner's ordered history and pinned module registration. Read
        # that exact checked-in module resource, verifying the registered digest;
        # never replace absent package metadata with a claimed installed pass.
        migrations = sorted(migration_owner.MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))
        migration_owner.validate_migration_history(migrations, [], expected_version=migration_owner.POSTGRES_SCHEMA_VERSION)
        with psycopg.connect(**options) as raw:
            for path in migrations:
                registration = migration_owner.MODULE_MIGRATIONS.get(path.name)
                if registration:
                    source = (ROOT / 'database' / registration[2]).read_bytes()
                    assert hashlib.sha256(source).hexdigest() == registration[3]
                    assert '-- sha256: ' + registration[3] in path.read_text()
                    raw.execute(source.decode(), prepare=False)
                raw.execute(path.read_text(), prepare=False)
                assert raw.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == int(path.name[:3])
        with psycopg.connect(**options) as raw:
            report["system_identifier"] = str(raw.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
            report["database_oid"] = raw.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[0]
            raw.execute("INSERT INTO settings(id) VALUES(1)")
            raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('white','Synthetic','fixture',%s)", (NOW.isoformat(),))
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))

        @contextmanager
        def transaction():
            with psycopg.connect(**options, row_factory=tempo_row_factory) as raw:
                raw.execute("SET LOCAL lock_timeout='3s'")
                raw.execute("SET LOCAL statement_timeout='5s'")
                assert raw.execute("SELECT current_database()").fetchone()[0] == database_name
                yield PostgresConnection(raw)

        yield transaction
    finally:
        # Check uncertain startup outcomes by exact name; never prune or touch
        # a different task resource. Preserve identity evidence even on failure.
        found = docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        if found:
            current = json.loads(docker("inspect", container).stdout)[0]
            assert current["Config"]["Labels"]["tempo.owner"] == identity
            assert current["Config"]["Labels"]["tempo.disposable"] == "true"
            # Evidence is best effort; a failed observation must never prevent
            # the exact verified fixture from reaching its existing teardown.
            report['memory_observations'] = {}
            for metric in ('memory.events', 'memory.peak'):
                try:
                    observed = docker('exec', current['Id'], 'cat', '/sys/fs/cgroup/' + metric, check=False)
                    report['memory_observations'][metric] = {
                        'exit_code': observed.returncode, 'value': observed.stdout[:4096]}
                except Exception as error:
                    report['memory_observations'][metric] = {'error_class': type(error).__name__}
            logs = docker("logs", container, check=False)
            (evidence / 'postgres.log').write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", current["Id"])
        assert not docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        report["cleanup_verified"] = True
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))
        secret.unlink(missing_ok=True)


@dataclass
class Case:
    transaction: object
    lapse: GameLapse
    decisions: Decisions
    card_id: str
    initial: tuple
    resources: object = None
    qualified: object = None

    def service(self, database, *, now=NOW, store=None):
        return GameFeedback(store or PostgresGameFeedbackStore(database), self.decisions, clock=lambda: now)


@pytest.fixture
def case(owned_postgres):
    identity = uuid.uuid4().hex
    card_id = CARD + "-" + identity
    lapse = fixture("lapse", GameLapse)
    lapse = lapse.model_copy(update={
        "key": lapse.key.model_copy(update={"game_id": identity}),
        "finding": lapse.finding.model_copy(update={"game_id": identity, "presentation_id": card_id}),
    })
    decisions = Decisions()
    decisions.mappings = (decisions.mappings[0].model_copy(update={"presentation_id": card_id}),)
    state = MemoryStore().card
    with owned_postgres() as database:
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
            VALUES(%s,'white','prefix',%s,'["e2e4"]','mature',%s,%s,1,20,%s,%s,20,'opening')""",
            (card_id, lapse.finding.position_key + " 0 1", state.due_date,
             (NOW - timedelta(days=6)).date().isoformat(), state.fsrs_card_json, state.first_correct_at))
        database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref) VALUES(%s,'correct',%s,10,20,'good',0,'study',%s)""",
            (card_id, (NOW-timedelta(days=3)).isoformat(), "original-history:" + identity))
        initial = tuple(database.execute_native("SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s", (card_id,)).fetchone())
    with ExitStack() as resources:
        yield Case(owned_postgres,lapse,decisions,card_id,initial,resources)


def counts(database, case):
    return tuple(database.execute_native("""SELECT
        (SELECT COUNT(*) FROM study_game_feedback WHERE effect_key=%s),
        (SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'),
        (SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='study')""",
        (effect_id(case.lapse.key), case.card_id, case.card_id)).fetchone())


def test_native_exact_retry_has_one_receipt_effect_and_original_deadline(case):
    with case.transaction() as database:
        first = case.service(database).accept_lapse(case.lapse)
    with case.transaction() as database:
        retry = case.service(database, now=NOW+timedelta(days=1)).accept_lapse(case.lapse)
        assert counts(database, case) == (1, 1, 1)
        assert retry.owner_receipt_id == first.owner_receipt_id and retry.available_by == first.available_by
        assert database.execute_native("SELECT COUNT(*) FROM study_attempts WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0


def test_native_concurrent_same_key_admission_has_one_effect(case):
    barrier = Barrier(2)
    def deliver():
        barrier.wait(timeout=5)
        with case.transaction() as database:
            return case.service(database).accept_lapse(case.lapse)
    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = [f.result(timeout=10) for f in (executor.submit(deliver), executor.submit(deliver))]
    assert {a.outcome, b.outcome} == {"accepted", "duplicate"}
    with case.transaction() as database:
        assert counts(database, case) == (1, 1, 1)
        assert a.owner_receipt_id == b.owner_receipt_id and a.available_by == b.available_by


def test_native_failure_after_schedule_before_receipt_rolls_back(case):
    with pytest.raises(RuntimeError, match="before effect receipt"):
        with case.transaction() as database:
            store = PostgresGameFeedbackStore(database)
            def fail(effect, now):
                assert counts(database, case) == (0, 1, 1)
                raise RuntimeError("before effect receipt")
            store.save = fail
            case.service(database, store=store).accept_lapse(case.lapse)
    with case.transaction() as database:
        assert counts(database, case) == (0, 0, 1)
        assert tuple(database.execute_native("SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s", (case.card_id,)).fetchone()) == case.initial
        assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 0
        assert case.service(database).accept_lapse(case.lapse).status == "applied"


def test_native_restart_readback_and_off_day_queue_recovery(case):
    with case.transaction() as database:
        first = case.service(database).accept_lapse(case.lapse)
        database.execute_native("DELETE FROM daily_queue WHERE card_id=%s", (case.card_id,))
    day = (NOW+timedelta(days=3)).date().isoformat()
    with case.transaction() as database:
        # New connection/application instance; queue rebuild never needs Games.
        resume_available_feedback(database, day)
        replay = case.service(database).effect_status(case.lapse.key)
        assert replay.owner_receipt_id == first.owner_receipt_id and replay.available_by == first.available_by
        assert database.execute_native("SELECT gameplay_priority_reason FROM daily_queue WHERE card_id=%s AND queue_date=%s", (case.card_id, day)).fetchone()[0] == "product_game_lapse"
        assert counts(database, case) == (1, 1, 1)


def test_native_pre_delivery_hold_survives_restart_and_delayed_original(case):
    control = fixture("hold", GameEffectControl).model_copy(update={"key": case.lapse.key})
    assert control.withdrawal is None
    with case.transaction() as database:
        first = case.service(database).control_effect(control)
    with case.transaction() as database:
        service = case.service(database)
        assert service.accept_lapse(case.lapse).status == "held"
        service.maintain()
        assert service.effect_status(case.lapse.key).owner_receipt_id == first.owner_receipt_id
        assert counts(database, case) == (1, 0, 1)


def test_native_timestamp_only_review_cannot_settle_without_real_issuance(case):
    with case.transaction() as database:
        service = case.service(database)
        service.accept_lapse(case.lapse)
        review_id = database.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,
            previous_interval,next_interval,internal_rating,guided,source_kind,source_ref)
            VALUES(%s,'correct',%s,0,1,'good',0,'study',%s) RETURNING id""",
            (case.card_id, (NOW+timedelta(hours=1)).isoformat(), 'actual-review:' + case.card_id)).fetchone()[0]
        settle_saved_review(database, case.card_id, review_id, 1)
        settle_saved_review(database, case.card_id, review_id, 1)
        effect = PostgresGameFeedbackStore(database).get(case.lapse.key)
        # A fabricated standalone review row is not an authentic new attempt.
        # The ordinary issue/save/replay path below supplies the real receipt.
        assert effect.status == 'applied' and effect.settlement_review_id is None
        assert counts(database, case) == (1, 1, 2)
        database.execute_native("UPDATE reviews SET invalidated_at=%s WHERE id=%s",
                                ((NOW+timedelta(hours=2)).isoformat(), review_id))
        invalidate_saved_review(database, case.card_id, review_id)
        effect = PostgresGameFeedbackStore(database).get(case.lapse.key)
        assert effect.status == 'applied' and effect.settlement_review_id is None
        service.maintain()
        assert counts(database, case) == (1, 1, 2)  # Reopening never repeats Again.


def _qualified_policy_owner(case, database, qualification, *, now=NOW, store=None):
    """Real storage/scheduler proof; global source authority has separate cases."""
    from app.study.encounter_feedback import EncounterFeedback
    class Current:
        def current_qualified(self, command, witness):
            assert witness.matches(command)
            return (witness.selection.decision,)
    return EncounterFeedback(store or PostgresGameFeedbackStore(database),Current(),
        clock=lambda:now,introductions=None,introduction_policy=None)


def test_native_introduced_first_departure_has_one_actual_again_receipt(case):
    from app.services.scheduling_review_application import apply_scheduling_review
    qualification = qualification_for(case.lapse)
    ordinary = MemoryStore()
    with case.transaction() as database:
        ordinary.card = PostgresGameFeedbackStore(database).scheduling.load_card_state(
            case.card_id,require_active=True)
    apply_scheduling_review(ordinary,case.card_id,'again',guided=False,source_kind='study',source_ref='normal',
        light_first_interval_days=7,reviewed_at=NOW,review_day=NOW.date())
    with case.transaction() as database:
        accepted = _qualified_policy_owner(case,database,qualification).accept_qualified(
            case.lapse,qualification=qualification)
        assert accepted.admission_status == 'pending'
    with case.transaction() as database:
        database.execute_native("SET LOCAL transaction_timeout='250ms'")
        first = _qualified_policy_owner(case,database,qualification).refresh_encounter(case.lapse.key)
        assert first.disposition == 'introduced_again' and first.admission_status == 'available'
        effect = PostgresGameFeedbackStore(database).get(case.lapse.key)
        assert effect.lapse == case.lapse and effect.encounter is None
        assert effect.review_id == first.progress.scheduling_review_id
        row = database.execute_native('SELECT due_date,interval_days,fsrs_card_json,first_correct_at,'
            'reinforcement_pending,stability,guided_review,state,scheduling_mode,hard_correct_streak,recent_attempts_json '
            'FROM cards WHERE id=%s',(case.card_id,)).fetchone()
        expected = ordinary.saved
        assert row['due_date'] == expected.due_date and row['interval_days'] == expected.interval_days
        assert json.loads(row['fsrs_card_json']) == json.loads(expected.fsrs_card_json)
        assert row['scheduling_mode'] == expected.scheduling_mode
        assert row['hard_correct_streak'] == expected.hard_correct_streak
        assert json.loads(row['recent_attempts_json']) == list(expected.recent_attempts)
        assert counts(database,case) == (1,1,1)
    with case.transaction() as database:
        replay = _qualified_policy_owner(case,database,qualification,now=NOW+timedelta(days=4))
        replay.refresh_encounter(case.lapse.key)
        assert replay.accept_qualified(case.lapse,qualification=qualification).owner_receipt_id == accepted.owner_receipt_id
        assert counts(database,case) == (1,1,1)
        database.execute_native('DELETE FROM daily_queue WHERE card_id=%s',(case.card_id,))


def test_native_qualified_schedule_receipt_failure_rolls_back(case):
    qualification = qualification_for(case.lapse)
    with case.transaction() as database:
        _qualified_policy_owner(case,database,qualification).accept_qualified(case.lapse,qualification=qualification)
    with pytest.raises(RuntimeError,match='after_actual_schedule'):
        with case.transaction() as database:
            store = PostgresGameFeedbackStore(database)
            def fail(effect, now):
                assert counts(database,case) == (1,1,1)
                raise RuntimeError('after_actual_schedule')
            store.save = fail
            _qualified_policy_owner(case,database,qualification,store=store).refresh_encounter(case.lapse.key)
    with case.transaction() as database:
        assert counts(database,case) == (1,0,1)
        assert tuple(database.execute_native('SELECT interval_days,fsrs_card_json,state,due_date '
            'FROM cards WHERE id=%s',(case.card_id,)).fetchone()) == case.initial
        assert PostgresGameFeedbackStore(database).get(case.lapse.key).status == 'pending'
        _qualified_policy_owner(case,database,qualification).refresh_encounter(case.lapse.key)
        assert counts(database,case) == (1,1,1)
        database.execute_native('DELETE FROM daily_queue WHERE card_id=%s',(case.card_id,))


def test_native_priority_preserves_unrelated_issued_attempt(case):
    with case.transaction() as database:
        head_card = case.card_id + '-active'
        database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,stability,content_type)
            SELECT %s,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,revision,
                interval_days,fsrs_card_json,stability,content_type FROM cards WHERE id=%s""",
            (head_card, case.card_id))
        head = database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,admission_kind)
            VALUES(%s,%s,0,'review') RETURNING id""", (NOW.date().isoformat(), head_card)).fetchone()[0]
        database.execute_native("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=%s", (head,))
        before = tuple(database.execute_native("SELECT fsrs_card_json,interval_days,state FROM cards WHERE id=%s", (head_card,)).fetchone())
        assert case.service(database).accept_lapse(case.lapse).status == 'applied'
        assert database.execute_native("SELECT position FROM daily_queue WHERE id=%s", (head,)).fetchone()[0] == 0
        assert tuple(database.execute_native("SELECT fsrs_card_json,interval_days,state FROM cards WHERE id=%s", (head_card,)).fetchone()) == before


def retained_owner_case(case, changed):
    """Actual existing source capture, no producer-supplied authority rows."""
    from app.legacy_game_observation import capture_game
    from app.services.postgres_integrity import current_integrity_certificate
    from tempo_game_evidence_contracts.legacy import decision_identity, fingerprint
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    import chess
    board = chess.Board()
    board.remove_piece_at(chess.A8 if changed else chess.D8)
    # Captures inspect actual catalogue alternatives across this module's DB.
    # Give independent fixtures distinct legal positions rather than accidentally
    # creating genuine cross-repertoire ambiguity for a later test.
    position_number = next(RETAINED_POSITION_SEQUENCE)
    assert position_number < 256
    for file in range(8):
        if position_number & (1 << file):
            board.remove_piece_at(chess.square(file,6))
    board.castling_rights = board.clean_castling_rights()
    assert board.is_valid()
    start = board.fen(en_passant='legal')
    position = ' '.join(start.split()[:4])
    repertoire = case.card_id + '-repertoire'
    route, scan = repertoire + '-route', repertoire + '-scan'
    introduced = (NOW-timedelta(days=6)).isoformat()
    with case.transaction() as database:
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Owner fixture','fixture',%s)", (repertoire,NOW.isoformat()))
        database.execute_native("UPDATE cards SET repertoire_id=%s,introduced_at=%s,start_fen=%s,trained_color='white' WHERE id=%s", (repertoire,introduced,start,case.card_id))
        database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)', (repertoire,case.card_id))
        database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
            VALUES(%s,%s,'Route','white',%s,'["e2e4"]',%s)""", (route,repertoire,start,NOW.isoformat()))
        database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (repertoire,NOW.isoformat()))
        database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
            card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,
            last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,0,%s,%s,%s,'["e2e4"]','white','prefix',0,0,%s)""",
            (repertoire,route,case.card_id,position,start,json.dumps([position])))
        for kind, identity in [('opening_graph_rebuild',repertoire+'-graph'),('integrity_scan',scan)]:
            database.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,
                payload_json,next_attempt_at,created_at,updated_at) VALUES(%s,%s,%s,1,'complete',%s,%s,%s,%s)""",
                (identity,kind,repertoire,json.dumps(dict(repertoire_id=repertoire,graph_generation=1)),
                 NOW.isoformat(),NOW.isoformat(),NOW.isoformat()))
        database.execute_native("""INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,
            scan_status,scan_generation,scan_completed_sources,scan_total_sources)
            VALUES(%s,'clean',%s,'idle',%s,1,1)""", (repertoire,NOW.isoformat(),scan+':1'))
        database.execute_native("""INSERT INTO imported_games(id,provider,provider_game_id,username,played_at,
            speed,rated,color,result,start_fen,moves_json,content_hash)
            VALUES(%s,'lichess',%s,'synthetic',%s,'rapid',0,'white','draw',%s,'["d2d4"]',%s)""",
            (case.lapse.key.game_id,case.lapse.key.game_id,(NOW-timedelta(days=1)).isoformat(),start,'a'*64))
    with case.transaction() as database:
        def reader(statement, parameters=()):
            return [dict(row) for row in database.execute_native(statement,parameters).fetchall()]
        def metadata(repertoire_id):
            row = database.execute_native("""SELECT repertoire.scope_source_revision,
                repertoire.canonical_prefix_revision,scope.generation catalogue_generation,
                publication.generation graph_generation,publication.state graph_state,
                integrity.status integrity_status,integrity.scan_status,integrity.scan_generation
                FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
                JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
                JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
                WHERE repertoire.id=%s AND scope.id=1""", (repertoire_id,)).fetchone()
            certificate=current_integrity_certificate(database,repertoire_id,1)
            return {**dict(row),'integrity_current':certificate is not None,'integrity_certificate':certificate}
        captured = capture_game(repertoire,case.lapse.key.game_id,(0,),reader=reader,metadata=metadata,now=NOW)
    decision = decision_identity(case.card_id,0,[])
    mapping = case.decisions.mappings[0].model_copy(update={
        'decision_id':decision,'repertoire_id':repertoire,'introduced_at':introduced,'position_key':position})
    snapshot = fixture('snapshot',RepertoireSnapshot).model_dump(mode='json')
    snapshot['membership'].update(workspace_id='legacy',snapshot_id='product:'+captured['export_digest'],
        digest=captured['export_digest'],source_revision=fingerprint(captured['authority']),
        scope_repertoire_ids=[repertoire],scope_id='capture:'+captured['export_digest'],
        routes=[dict(repertoire_id=repertoire,route_id=route,policy_id=repertoire,learner_color='white',
                     starting_fen=start,moves=['e2e4'],reentries=[])])
    snapshot['boundaries']=[dict(route_id=route,terminal_ply=1,deliberate=True)]
    snapshot['decision_presentations']=[mapping.model_dump(mode='json')]
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(snapshot))
    lapse=case.lapse.model_copy(update={
        'key':case.lapse.key.model_copy(update={'workspace_id':'legacy','decision_id':decision}),
        'finding':case.lapse.finding.model_copy(update={'repertoire_id':repertoire,'decision_id':decision,'position_key':position})})
    return repertoire,captured,snapshot,lapse


@pytest.mark.parametrize('changed',[False,True])
def test_native_current_owner_factory_fences_retained_rows_and_rejects_changed_authority(case, changed):
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback, CurrentDecisionUnavailable
    repertoire,captured,snapshot,lapse = retained_owner_case(case, changed)
    if changed:
        with case.transaction() as database:
            database.execute_native('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=%s', (repertoire,))
        with pytest.raises(CurrentDecisionUnavailable,match='revision_changed'):
            with case.transaction() as database:
                current_owner_game_feedback(database,retained_owner_capture=captured,snapshot=snapshot,
                                            clock=lambda:NOW).accept_lapse(lapse)
        with case.transaction() as database:
            assert PostgresGameFeedbackStore(database).get(lapse.key) is None
            assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'", (case.card_id,)).fetchone()[0] == 0
        return
    with case.transaction() as database:
        service=current_owner_game_feedback(database,retained_owner_capture=captured,snapshot=snapshot,clock=lambda:NOW)
        assert service.accept_lapse(lapse).status == 'applied'
        def concurrent_source_edit():
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with case.transaction() as editor:
                    editor.execute_native("SET LOCAL lock_timeout='100ms'")
                    editor.execute_native('UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=%s', (repertoire,))
            return 'fenced'
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(concurrent_source_edit).result(timeout=5) == 'fenced'
        assert service.effect_status(lapse.key).status == 'applied'


def test_native_day_only_introduction_requires_exact_current_game_proof(case):
    """Reproduce #488's date-only retained capture through the actual owner port."""
    from app.legacy_game_observation import capture_game
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    from tempo_game_evidence_contracts.legacy import fingerprint
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback, CurrentDecisionUnavailable
    _, capture, snapshot, lapse = retained_owner_case(case, False)
    introduced_day=(NOW-timedelta(days=6)).date().isoformat()
    with case.transaction() as database:
        database.execute_native('UPDATE cards SET introduced_at=%s WHERE id=%s',(introduced_day,case.card_id))
    metadata={**capture['authority'],'integrity_current':True,
              'integrity_certificate':capture['catalogue_scope']['integrity_certificate']}
    with case.transaction() as database:
        def reader(statement,parameters=()):
            return [dict(row) for row in database.execute_native(statement,parameters).fetchall()]
        capture=capture_game(capture['authority']['repertoire_id'],capture['game']['id'],(0,),
            reader=reader,metadata=lambda _:metadata,now=NOW)
    payload=snapshot.model_dump(mode='json')
    payload['membership'].update(digest=capture['export_digest'],source_revision=fingerprint(capture['authority']))
    payload['decision_presentations'][0].update(introduced_at=None,introduced_before_game=True,
        introduction_game=dict(provider=lapse.key.provider,game_id=lapse.key.game_id,
                               source_revision=lapse.finding.source_revision,source_digest=lapse.finding.source_digest))
    snapshot=RepertoireSnapshot.model_validate_json(json.dumps(payload))
    assert snapshot.decision_presentations[0].introduced_at is None

    assert capture['tables']['cards'][0]['introduced_before_game'] is True
    with case.transaction() as database:
        service=current_owner_game_feedback(database,retained_owner_capture=capture,snapshot=snapshot,clock=lambda:NOW)
        assert service.accept_lapse(lapse).status=='applied'
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0]==introduced_day
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0]==1
    # A boolean from any different source is not chronology for this effect.
    for field,value in [('provider','chesscom'),('game_id','another-game'),
                        ('source_revision',lapse.finding.source_revision+1),('source_digest','e'*64)]:
        altered=snapshot.model_dump(mode='json')
        altered['decision_presentations'][0]['introduction_game'][field]=value
        wrong=RepertoireSnapshot.model_validate_json(json.dumps(altered))
        with case.transaction() as database:
            store=PostgresGameFeedbackStore(database)
            store.lock_target(case.card_id,NOW.date().isoformat())
            owner=current_owner_game_feedback(database,retained_owner_capture=capture,snapshot=wrong,clock=lambda:NOW)
            with pytest.raises(CurrentDecisionUnavailable,match='introduction_game_scope_mismatch'):
                owner.decisions.current(lapse)
            assert store.get(lapse.key).status=='applied'
    assert snapshot.decision_presentations[0].introduced_at is None


def _qualify_case(case,capture,command):
    from product_encounter_fixtures import qualified_feedback_source
    case.resources.close()
    case.qualified = case.resources.enter_context(qualified_feedback_source(
        case.transaction,capture=capture,command=command))


def current_encounter_case(case, *, unintroduced=True, matched=False, prepare=True):
    """Published owners only: real retained capture, mapping, canonical admission."""
    from app.legacy_game_observation import capture_game
    from app.contracts.product_v02.learning import GameEncounter
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    from tempo_game_evidence_contracts.legacy import fingerprint
    repertoire,capture,snapshot,lapse = retained_owner_case(case, False)
    with case.transaction() as database:
        assert database.execute_native("""SELECT
            to_regclass('study_product_introduction_state') IS NULL AND
            to_regclass('study_product_introduction_periods') IS NULL AND
            to_regclass('study_product_introduction_charges') IS NULL AND
            to_regclass('study_product_introduction_rankings') IS NULL""").fetchone()[0]
        database.execute_native('UPDATE settings SET study_only_mode=0,new_cards_per_day=100 WHERE id=1')
        if unintroduced:
            database.execute_native("UPDATE cards SET introduced_at=NULL,state='new',fsrs_card_json=NULL,"
                "interval_days=0,first_correct_at=NULL,stability=0 WHERE id=%s",(case.card_id,))
            database.execute_native('DELETE FROM reviews WHERE card_id=%s',(case.card_id,))
        if matched:
            database.execute_native("UPDATE imported_games SET moves_json='[\"e2e4\"]' WHERE id=%s",(lapse.key.game_id,))
    metadata={**capture['authority'],'integrity_current':True,
              'integrity_certificate':capture['catalogue_scope']['integrity_certificate']}
    with case.transaction() as database:
        def reader(statement,parameters=()):
            return [dict(row) for row in database.execute_native(statement,parameters).fetchall()]
        capture=capture_game(repertoire,lapse.key.game_id,(0,),reader=reader,metadata=lambda _:metadata,now=NOW)
    payload=snapshot.model_dump(mode='json')
    payload['membership'].update(digest=capture['export_digest'],source_revision=fingerprint(capture['authority']))
    mapping=payload['decision_presentations'][0]
    mapping.update(route_id=payload['membership']['routes'][0]['route_id'],route_ply=0,
        introduction='unintroduced' if unintroduced else 'introduced',
        introduced_at=None if unintroduced else mapping['introduced_at'],
        introduction_matches_current_content=not unintroduced)
    snapshot=RepertoireSnapshot.model_validate_json(json.dumps(payload))
    from app.product_composition.retained_game import retained_canonical_game
    canonical = retained_canonical_game(capture)
    command=GameEncounter(key=lapse.key,
        source=dict(provider=lapse.key.provider,game_id=lapse.key.game_id,
            source_revision=canonical.source_revision,source_digest=canonical.source_digest),
        decision=snapshot.decision_presentations[0],expected_uci='e2e4',
        played_uci='e2e4' if matched else 'd2d4',observation='matched' if matched else 'missed',
        detected_at=NOW.isoformat(),occurred_at=canonical.played_at,payload_digest='e'*64)
    if not matched and prepare:
        _qualify_case(case,capture,command)
    return capture,snapshot,command


def current_encounter_service(database, capture, snapshot, *, case=None, now=NOW, include_introductions=True, paused=False):
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_feedback
    introductions=None
    if include_introductions:
        from app.services.queue_admission_adapter import current_owner_encounter_introductions
        introductions=current_owner_encounter_introductions(database,allowance='encounter_exception',paused=paused,
            prepared_introduction=case.qualified.prepared_introduction if case and case.qualified else None)
    owner = current_owner_encounter_feedback(database,retained_owner_capture=capture,snapshot=snapshot,
        clock=lambda:now,introductions=introductions,introduction_policy=EncounterIntroductionPolicy('encounter_exception'),
        prepared_decisions=case.qualified.prepared.prepared_decisions if case and case.qualified else None,
        prepared_priority=case.qualified.prepared_priority if case and case.qualified else None)
    if case and case.qualified:
        owner.test_qualification = lambda command:case.qualified.prepared.qualification
    return owner


@pytest.mark.parametrize('matched',[False,True])
def test_native_encounter_current_owner_introduces_once_and_persists_original_without_review(case, matched):
    capture,snapshot,command=current_encounter_case(case,matched=matched)
    with case.transaction() as database:
        service=current_encounter_service(database,capture,snapshot,case=case,include_introductions=True)
        receipt=accept_and_refresh(service,command)
        if matched:
            assert receipt.disposition=='frequency_only' and receipt.admission_status=='not_requested'
            assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0] is None
            assert database.execute_native('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
            assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
            assert PostgresGameFeedbackStore(database).get(command.key).encounter==command
            replay=service.accept_encounter(command)
            assert replay.outcome=='duplicate' and replay.owner_receipt_id==receipt.owner_receipt_id
            return
        assert receipt.disposition=='first_introduction' and receipt.admission_status=='available'
        state=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        assert state[0]==NOW.date().isoformat() and state[1]=='learning'
        queue=database.execute_native('SELECT admission_kind,attempt_state,gameplay_priority_reason FROM daily_queue WHERE card_id=%s',
            (case.card_id,)).fetchone()
        assert tuple(queue)==('new','clean','product_game_encounter')
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0]==0
        assert database.execute_native('SELECT COUNT(*) FROM study_attempts WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
    with case.transaction() as database:
        service=current_encounter_service(database,capture,snapshot,case=case,now=NOW+timedelta(days=1))
        replay=service.accept_encounter(command.model_copy(update={'detected_at':(NOW+timedelta(days=1)).isoformat()}))
        assert replay.owner_receipt_id==receipt.owner_receipt_id and replay.detected_at==command.detected_at
        assert replay.outcome=='duplicate' and replay.observation_recorded
        assert tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())==state
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=%s AND introduced_at IS NOT NULL',
            (case.card_id,)).fetchone()[0]==1
        assert PostgresGameFeedbackStore(database).get(command.key).encounter==command
        assert service.encounter_status(command.key).admission_status=='available'


def test_native_encounter_failed_refresh_preserves_accepted_receipt_and_rolls_back_introduction(case):
    capture,snapshot,command=current_encounter_case(case)
    with case.transaction() as database:
        accepted=current_encounter_service(database,capture,snapshot,case=case).accept_qualified(
            command,qualification=case.qualified.prepared.qualification)
        assert accepted.admission_status=='pending'
    with pytest.raises(RuntimeError,match='after_canonical_introduction'):
        with case.transaction() as database:
            service=current_encounter_service(database,capture,snapshot,case=case)
            def fail_save(*args): raise RuntimeError('after_canonical_introduction')
            service.store.save=fail_save
            service.refresh_encounter(command.key)
    with case.transaction() as database:
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0] is None
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=%s AND introduced_at IS NOT NULL',
            (case.card_id,)).fetchone()[0]==0
        retained=PostgresGameFeedbackStore(database).get(command.key)
        assert retained.status=='pending' and retained.encounter==command
        assert current_encounter_service(database,capture,snapshot,case=case).refresh_encounter(command.key).admission_status=='available'


def test_native_encounter_ordinary_introduction_race_preserves_current_progress_and_single_charge(case):
    capture,snapshot,command=current_encounter_case(case)
    with case.transaction() as database:
        accepted=current_encounter_service(database,capture,snapshot,case=case).accept_qualified(
            command,qualification=case.qualified.prepared.qualification)
        assert accepted.disposition=='first_introduction' and accepted.admission_status=='pending'
    with case.transaction() as database:
        from app.services.queue_admission_adapter import current_owner_encounter_introductions
        ordinary=current_owner_encounter_introductions(database,allowance='ordinary',paused=False)
        target=command.decision.model_copy(update={
            'repertoire_revision':capture['authority']['scope_source_revision']+1})
        assert ordinary.admit_encounter(NOW.date().isoformat(),
            repertoire_id=command.decision.repertoire_id,decision_id=command.key.decision_id,target=target).status=='introduced'
        state=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
    with case.transaction() as database:
        # Original capture still says unintroduced; only that current field may
        # race. Definition/revision/source/membership fences remain exact.
        receipt=current_encounter_service(database,capture,snapshot,case=case).refresh_encounter(command.key)
        assert receipt.disposition=='first_introduction' and receipt.admission_status=='available'
        assert tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())==state
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=%s AND introduced_at IS NOT NULL',
            (case.card_id,)).fetchone()[0]==1
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",(case.card_id,)).fetchone()[0]==0


def test_native_encounter_saved_pause_and_pre_admission_hold_remain_durable(case):
    capture,snapshot,command=current_encounter_case(case)
    with case.transaction() as database:
        database.execute_native('UPDATE settings SET study_only_mode=1,new_cards_per_day=0 WHERE id=1')
        receipt=accept_and_refresh(current_encounter_service(database,capture,snapshot,case=case,paused=True),command)
        assert receipt.admission_status=='pending' and receipt.deferred_reason=='paused' and receipt.available_by is None
        assert receipt.observation_recorded and receipt.detected_at==command.detected_at
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0] is None
    with case.transaction() as database:
        service=current_encounter_service(database,capture,snapshot,case=case)
        service.control_effect(GameEffectControl(key=command.key,source_revision=2,action='hold',reason='reanalysis'))
    with case.transaction() as database:
        database.execute_native('UPDATE settings SET study_only_mode=0 WHERE id=1')
        service=current_encounter_service(database,capture,snapshot,case=case)
        assert service.accept_encounter(command).status=='held'
        service.maintain(after=effect_id(command.key)[:-1])
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0] is None
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=%s AND introduced_at IS NOT NULL',
            (case.card_id,)).fetchone()[0]==0


def test_native_encounter_historical_miss_applies_current_again_preserving_human_history(case):
    capture,snapshot,command=current_encounter_case(case,unintroduced=False)
    with case.transaction() as database:
        # This legitimate later introduction/learning is current owner state.
        # Qualified first departure applies one current Again while retaining
        # the actual introduction and all prior human review rows.
        later=(NOW-timedelta(hours=2)).isoformat()
        database.execute_native('UPDATE cards SET introduced_at=%s WHERE id=%s',(later,case.card_id))
        before=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json,first_correct_at FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        history=database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]
        service=current_encounter_service(database,capture,snapshot,case=case,include_introductions=False)
        receipt=accept_and_refresh(service,command)
        assert receipt.disposition=='introduced_again' and receipt.observation_recorded
        assert receipt.admission_status=='available'
        after=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json,first_correct_at FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        assert after[0]==before[0] and after[1:4]!=before[1:4]
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]==history+1
        assert receipt.progress.scheduling_review_id is not None
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE id=%s AND introduced_at IS NOT NULL',
            (case.card_id,)).fetchone()[0]==1
        assert PostgresGameFeedbackStore(database).get(command.key).encounter==command
        assert service.encounter_status(command.key).admission_status=='available'
        service.control_effect(GameEffectControl(key=command.key,source_revision=2,action='hold',reason='reanalysis'))
        assert PostgresGameFeedbackStore(database).get(command.key).encounter==command
        assert service.encounter_status(command.key).status=='held'


def test_native_encounter_correct_introduced_play_records_frequency_only(case):
    capture,snapshot,command=current_encounter_case(case,unintroduced=False,matched=True)
    with case.transaction() as database:
        before=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        history=database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]
        service=current_encounter_service(database,capture,snapshot,case=case,include_introductions=False)
        receipt=accept_and_refresh(service,command)
        assert receipt.observation_recorded and receipt.disposition=='frequency_only'
        assert receipt.admission_status=='not_requested' and receipt.available_by is None
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]==history
        assert tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())==before
        assert PostgresGameFeedbackStore(database).get(command.key).encounter==command


@pytest.mark.parametrize('ordinary_limit,study_only',[(0,False),(0,True),(1,True)])
def test_native_encounter_targeted_exception_preserves_ordinary_settings(case, ordinary_limit, study_only):
    if ordinary_limit:
        with case.transaction() as database:
            database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                introduced_at,revision,content_type) SELECT %s,repertoire_id,kind,start_fen,moves_json,'learning',
                due_date,%s,revision,content_type FROM cards WHERE id=%s""",
                (case.card_id+'-ordinary-spent',NOW.date().isoformat(),case.card_id))
    capture,snapshot,command=current_encounter_case(case)
    with case.transaction() as database:
        database.execute_native('UPDATE settings SET new_cards_per_day=%s,study_only_mode=%s WHERE id=1',
            (ordinary_limit,int(study_only)))
        database.execute_native('UPDATE repertoires SET new_cards_per_day=0 WHERE id=%s',
            (command.decision.repertoire_id,))
        before=tuple(database.execute_native('SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1').fetchone())
    case.qualified.reprepare()
    with case.transaction() as database:
        service=current_encounter_service(database,capture,snapshot,case=case)
        receipt=accept_and_refresh(service,command)
        assert receipt.admission_status=='available' and receipt.disposition=='first_introduction'
        assert tuple(database.execute_native('SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1').fetchone())==before
        assert database.execute_native('SELECT new_cards_per_day FROM repertoires WHERE id=%s',
            (command.decision.repertoire_id,)).fetchone()[0]==0
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(case.card_id,)).fetchone()[0]==1

@pytest.mark.parametrize('day_offset',[0,1])
def test_native_encounter_normal_study_seed_preserves_targeted_introduction(case, day_offset):
    capture,snapshot,command=current_encounter_case(case)
    with case.transaction() as database:
        database.execute_native('UPDATE settings SET new_cards_per_day=0,study_only_mode=1 WHERE id=1')
        database.execute_native('UPDATE repertoires SET new_cards_per_day=0 WHERE id=%s',
            (command.decision.repertoire_id,))
    case.qualified.reprepare()
    with case.transaction() as database:
        service=current_encounter_service(database,capture,snapshot,case=case)
        assert accept_and_refresh(service,command).admission_status=='available'
        before=tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        from app.services.queue_application import seed_queue,presentation_rows
        study_day=(NOW+timedelta(days=day_offset)).date().isoformat()
        seed_queue(database,study_day)
        assert tuple(database.execute_native('SELECT introduced_at,state,interval_days,fsrs_card_json FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())==before
        assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s AND queue_date=%s AND status='queued'",
            (case.card_id,study_day)).fetchone()[0]==1
        current=next(row for row in presentation_rows(database,study_day) if row['card_id']==case.card_id)
        assert current['admission_kind']=='new' and current['gameplay_priority_reason']=='product_game_encounter'
        assert database.execute_native('SELECT COUNT(*) FROM reviews WHERE card_id=%s',(case.card_id,)).fetchone()[0]==0


def equivalent_current_encounter_case(case, *, aliases=62, unintroduced=True):
    """Complete actual route/graph aliases, projected from a fresh owner capture."""
    from app.legacy_game_observation import capture_game
    from app.repertoire import project_capture
    from app.services.postgres_integrity import current_integrity_certificate
    from tempo_legacy_database.adapters.games.authority.headers import repertoire_metadata
    capture, _, command = current_encounter_case(case, unintroduced=unintroduced,prepare=False)
    repertoire = command.decision.repertoire_id
    route = capture['tables']['repertoire_lines'][0]['id']
    with case.transaction() as database:
        for number in range(1, aliases):
            alias = route + '-alias-' + str(number).zfill(2)
            database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,
                start_fen,moves_json,created_at) SELECT %s,repertoire_id,name,trained_color,start_fen,
                moves_json,created_at FROM repertoire_lines WHERE id=%s""", (alias, route))
            database.execute_native("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,
                decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,
                first_decision_index,last_decision_index,decision_fen_keys_json)
                SELECT repertoire_id,generation,%s,decision_index,card_id,decision_fen_key,starting_fen,
                moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,
                decision_fen_keys_json FROM opening_graph_steps WHERE line_id=%s""", (alias,route))
    with case.transaction() as database:
        def reader(statement, parameters=()):
            return [dict(row) for row in database.execute_native(statement,parameters).fetchall()]
        def metadata(identity):
            header = repertoire_metadata(reader, identity)[0]
            certificate = current_integrity_certificate(database, identity, header['graph_generation'])
            return dict(header,integrity_current=certificate is not None,integrity_certificate=certificate)
        capture = capture_game(repertoire,command.key.game_id,(0,),reader=reader,metadata=metadata,now=NOW)
    snapshot = project_capture(capture)
    assert len(snapshot.decision_presentations) == len(capture['graphs']) == aliases
    assert len({item.presentation_id for item in snapshot.decision_presentations}) == 1
    mapping = min(snapshot.decision_presentations, key=lambda item:(item.route_id,item.route_ply))
    command = command.model_copy(update={'decision':mapping})
    return capture,snapshot,command


@pytest.mark.parametrize('aliases',[2,62])
@pytest.mark.parametrize('unintroduced',[False,True])
def test_native_equivalent_route_bindings_apply_once_and_preserve_all_provenance(case,aliases,unintroduced):
    capture,snapshot,command = equivalent_current_encounter_case(case,aliases=aliases,unintroduced=unintroduced)
    original = snapshot.model_dump_json()
    # The receiver proves the supplied original member, not an arbitrary first alias.
    command = command.model_copy(update={'decision':max(snapshot.decision_presentations,
        key=lambda item:(item.route_id,item.route_ply))})
    _qualify_case(case,capture,command)
    with case.transaction() as database:
        before = tuple(database.execute_native('SELECT introduced_at,fsrs_card_json,interval_days FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        service = current_encounter_service(database,capture,snapshot,case=case)
        receipt = accept_and_refresh(service,command)
        assert receipt.admission_status == 'available'
        assert receipt.disposition == ('first_introduction' if unintroduced else 'introduced_again')
        after = tuple(database.execute_native('SELECT introduced_at,fsrs_card_json,interval_days FROM cards WHERE id=%s',
            (case.card_id,)).fetchone())
        assert after[0] == (NOW.date().isoformat() if unintroduced else before[0])
        assert (after[1:] == before[1:]) is unintroduced
        assert snapshot.model_dump_json() == original
        assert len(service.decisions.snapshot.decision_presentations) == aliases
    alternate = next(item for item in snapshot.decision_presentations if item.route_id != command.decision.route_id)
    replay = command.model_copy(update={'decision':alternate,'detected_at':(NOW+timedelta(days=1)).isoformat()})
    with case.transaction() as database:
        receipt2 = current_encounter_service(database,capture,snapshot,case=case).accept_encounter(replay)
        assert receipt2.outcome == 'duplicate' and receipt2.owner_receipt_id == receipt.owner_receipt_id
        assert receipt2.detected_at == command.detected_at
        effect = PostgresGameFeedbackStore(database).get(command.key)
        assert effect.encounter == command
        assert database.execute_native('SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s',
            (case.card_id,)).fetchone()[0] == 1
        assert database.execute_native("SELECT COUNT(*) FROM daily_queue WHERE card_id=%s AND status='queued'",
            (case.card_id,)).fetchone()[0] == 1
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
            (case.card_id,)).fetchone()[0] == int(not unintroduced)
        assert len(service.decisions.snapshot.decision_presentations) == aliases


@pytest.mark.parametrize('invalid',['conflict','different_target','unavailable','missing_binding','changed_revision','unknown_supplied_route'])
def test_native_equivalent_route_bindings_fail_closed_for_conflict_or_staleness(case,invalid):
    capture,snapshot,command = equivalent_current_encounter_case(case,aliases=2)
    if invalid == 'changed_revision':
        with case.transaction() as database:
            database.execute_native('UPDATE cards SET revision=revision+1 WHERE id=%s',(case.card_id,))
    elif invalid == 'unknown_supplied_route':
        command = command.model_copy(update={'decision':command.decision.model_copy(update={'route_id':'not-current-route'})})
    else:
        mappings = list(snapshot.decision_presentations)
        if invalid == 'missing_binding':
            mappings.pop()
        else:
            update = ({'available':False} if invalid == 'unavailable' else
                      {'presentation_id':case.card_id+'-other'} if invalid == 'different_target' else
                      {'presentation_revision':command.decision.presentation_revision+1})
            mappings[1] = mappings[1].model_copy(update=update)
        snapshot = snapshot.model_copy(update={'decision_presentations':tuple(mappings)})
    with case.transaction() as database:
        receipt = current_encounter_service(database,capture,snapshot,case=case).accept_encounter(command)
        assert receipt.admission_status != 'available'
        assert receipt.status in {'pending','held'}
        assert database.execute_native('SELECT introduced_at FROM cards WHERE id=%s',(case.card_id,)).fetchone()[0] is None
        assert database.execute_native('SELECT COUNT(*) FROM daily_queue WHERE card_id=%s',(case.card_id,)).fetchone()[0] == 0
        assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
            (case.card_id,)).fetchone()[0] == 0
