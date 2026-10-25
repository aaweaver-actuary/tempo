"""Actual production Study screen; no mock queue/review APIs."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import hmac, json, os, subprocess, time, uuid
import pytest
from test_restart import server
from test_runtime import activate, bundle
from test_families import SPECIFICATIONS, defense_definition
from test_endgames import endgame_bundle


def publication_for(scenario):
    if scenario.startswith("typed-"):
        family = scenario.removeprefix("typed-").removesuffix("-loss")
        specification = next(specification for specification, _ in SPECIFICATIONS if specification["type"] == family)
        return bundle([("study_exercise", specification)])
    if scenario in ["tactic-review", "middlegame"]:
        return bundle([("tactic" if scenario == "tactic-review" else "middlegame", SPECIFICATIONS[0][0])])
    if scenario.startswith("defense"):
        definition = defense_definition()
        payload = bundle([("defense", {**SPECIFICATIONS[0][0], "accepted_lines": [["e1f2"]]})])
        payload["publication"]["definitions"][0]["starting_fen"] = definition["anchor"]["position"]["start_fen"]
        payload["facts"][0]["defense"] = definition
        return payload
    return endgame_bundle() if scenario.startswith("endgame") else bundle()



@contextmanager
def study_assets(control=None, control_key=None):
    class Assets(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            super().end_headers()

        def do_POST(self):
            if control is None or not control_key or not hmac.compare_digest(self.headers.get("X-Tempo-Fixture-Key", ""), control_key):
                self.send_error(403)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                self.send_error(413)
                return
            result = control(self.path, json.loads(self.rfile.read(length) or b"{}"))
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    assets = Path(os.environ["TEMPO_LEARNING_STUDY_ASSETS"])
    host = ThreadingHTTPServer(("127.0.0.1", 0), partial(Assets, directory=str(assets)))
    thread = Thread(target=host.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{host.server_port}"
    finally:
        host.shutdown()
        host.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("scenario", ["normal", "teaching", "guided-failure", "lost-response", "before-commit", "next-load", "two-tabs", "storage-denied", "origin-transition", "offline-shell", "tactic-review", "middlegame", "typed-move_line", "typed-square_set", "typed-knight_path", "typed-choice", "typed-explanation", "typed-explanation-loss", "defense", "defense-uncovered", "endgame-saved-line", "endgame-material-unavailable", "settings", "wrong-credential", "wrong-workspace", "disallowed-origin", "quota", "aborted-storage", "incompatible-storage", "tactics-discovery", "tactics-discovery-loss", "delayed-replacement", "authority-reconnect", "service-unavailable-browser-online", "multi-authority-resume", "registered-authority-recovery", "study-authoring-boundary", "bootstrap-body-interruption-browser-online"])
def test_production_study_learning_native(case, tmp_path, scenario):
    activate(case, publication_for(scenario))
    extra = {}
    if scenario == "endgame-material-unavailable":
        # Historical material-only cards carry a position but no authored line.
        case.query("UPDATE learning.cards SET moves_json='[]' WHERE content_type='endgame'")
    if scenario not in ["teaching", "next-load"]:
        case.query("UPDATE learning.cards SET first_correct_at='2026-01-01T12:00:00Z'")
    root = Path(__file__).resolve().parents[5]
    fixture_key = uuid.uuid4().hex
    replacement_credential = "replacement-study-" + uuid.uuid4().hex + uuid.uuid4().hex

    def control(path, body):
        assert scenario == "authority-reconnect" and path == "/__fixture/rotate-study-credential"
        (tmp_path / "credential").write_text(replacement_credential)
        return process.restart_service()

    with study_assets(control, fixture_key) as origin, server(case, tmp_path, origins=(() if scenario == "disallowed-origin" else (origin,))) as (client, process):
        result = subprocess.run(["node", str(root / "scripts/prove-study-learning.mjs")],
            input=json.dumps({"frontend": origin, "learning": str(client.base_url).rstrip("/"),
                "credential": case.configuration.token, "replacement_credential": replacement_credential,
                "fixture_key": fixture_key, "scenario": scenario, **extra}),
            cwd=root, capture_output=True, text=True, timeout=90)
        (tmp_path / f"study-proof-{scenario}.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
        proof = json.loads(result.stdout)
        assert proof["complete"] is True
        assert not proof["unexpected_requests"]
    reviews = case.query("SELECT COUNT(*) FROM learning.reviews")[0][0]
    assert reviews == (0 if scenario in ["storage-denied", "origin-transition", "defense-uncovered", "endgame-material-unavailable", "settings", "wrong-credential", "wrong-workspace", "disallowed-origin", "quota", "aborted-storage", "incompatible-storage", "tactics-discovery", "tactics-discovery-loss", "multi-authority-resume", "registered-authority-recovery", "study-authoring-boundary"] else 1)
    if scenario in ["multi-authority-resume", "registered-authority-recovery"]:
        # The two browser authorities forward to one installed Learning database;
        # this proves origin isolation and real receipt recovery, not two DBs.
        assert len(proof["confirmed_receipts"]) == 2
        for receipt in proof["confirmed_receipts"]:
            actual = case.client.get("/api/operations/" + receipt["operation_id"]).json()
            assert actual["state"] == "complete" and actual["response"] == receipt["response"]
            assert case.query("SELECT COUNT(*) FROM learning.operation_receipts WHERE operation_id=%s AND state='complete'", (receipt["operation_id"],))[0][0] == 1
        assert case.client.get("/api/study/settings").json()["tactics_new_per_day"] == 3
    if scenario == "teaching":
        assert case.query("SELECT COUNT(*) FROM learning.teaching_states")[0][0] == 1
    if reviews and not scenario.startswith(("typed-", "defense")):
        assert case.query("SELECT COUNT(*) FROM learning.review_attempt_receipts")[0][0] == 1
    if scenario.startswith("typed-"):
        assert case.query("SELECT COUNT(*) FROM learning.study_attempts")[0][0] == 1
    if scenario == "defense":
        assert case.query("SELECT COUNT(*) FROM learning.defense_attempts")[0][0] == 1
    if scenario == "guided-failure":
        assert tuple(case.query("SELECT rating,guided FROM learning.reviews")[0]) == ("again", 1)
    if scenario.startswith("tactics-discovery"):
        assert case.query("SELECT COUNT(*) FROM learning.tactic_discovery_attempts")[0][0] == 1
    if scenario in ["settings", "authority-reconnect"]:
        assert case.client.get("/api/study/settings").json()["tactics_new_per_day"] == 3
    if scenario == "defense-uncovered":
        assert case.query("SELECT COUNT(*) FROM learning.analysis_obligations")[0][0] == 1


@pytest.mark.parametrize("late", [False, True], ids=["producer-stopped-before-study", "late-after-issued-attempt"])
def test_production_study_settles_real_issuance_bound_remediation(case, producer, tmp_path, late):
    from test_game_evidence_runtime import activate_without_queue_work
    payload, activation_id, receipt = activate_without_queue_work(case)
    producer.load(payload, activation_id, receipt)
    case.query("UPDATE learning.cards SET first_correct_at='2026-01-01T12:00:00Z'")
    fixture_key = uuid.uuid4().hex
    endpoint = None
    delivered = False

    def control(path, body):
        nonlocal delivered
        if path == "/__fixture/deliver-evidence":
            assert not delivered
            producer.work(endpoint + "/internal/evidence")
            delivered = True
            assert producer.query("SELECT status FROM outbox")[0][0] == "completed"
            return {"producer_stopped": True}
        if path == "/__fixture/assert-late-pending":
            assert case.query("SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled")[0][0] == 0
            return {"settled": 0}
        raise AssertionError("Unexpected fixture control")

    root = Path(__file__).resolve().parents[5]
    with study_assets(control, fixture_key) as origin, server(case, tmp_path, maintenance=True, origins=(origin,)) as (client, process):
        endpoint = str(client.base_url).rstrip("/")
        if not late:
            control("/__fixture/deliver-evidence", {})
        result = subprocess.run(["node", str(root / "scripts/prove-study-learning.mjs")],
            input=json.dumps({"frontend": origin, "learning": endpoint, "credential": case.configuration.token,
                "scenario": "remediation-late" if late else "remediation", "fixture_key": fixture_key}),
            cwd=root, capture_output=True, text=True, timeout=90)
        (tmp_path / ("study-proof-remediation-late.log" if late else "study-proof-remediation.log")).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
        assert json.loads(result.stdout)["complete"]
    assert delivered
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == (2 if late else 1)
    assert case.query("SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled")[0][0] == 1


@pytest.mark.parametrize("scenario", ["service-restart", "database-interruption"])
def test_production_study_interruption_retains_original_command(case, tmp_path, scenario):
    activate(case)
    case.query("UPDATE learning.cards SET first_correct_at='2026-01-01T12:00:00Z'")
    fixture_key = uuid.uuid4().hex
    process = None
    database_stopped = False
    identity = os.environ["TEMPO_LEARNING_TEST_FIXTURE"]

    def start_database():
        nonlocal database_stopped
        subprocess.run(["docker", "start", identity], check=True, capture_output=True)
        for _ in range(100):
            try:
                if case.client.get("/health/ready").status_code == 200:
                    database_stopped = False
                    return {"restarted": True}
            except Exception:
                pass
            time.sleep(0.05)
        raise AssertionError("Owned PostgreSQL did not recover")

    def control(path, body):
        nonlocal database_stopped
        if path == "/__fixture/restart-learning":
            return process.restart_service()
        if path == "/__fixture/stop-database":
            labels = json.loads(subprocess.check_output(["docker", "inspect", identity, "--format", "{{json .Config.Labels}}"], text=True))
            assert labels.get("tempo.owner") == identity and labels.get("tempo.disposable") == "true"
            database_stopped = True
            subprocess.run(["docker", "stop", "--time", "5", identity], check=True, capture_output=True)
            return {"stopped": True}
        if path == "/__fixture/start-database":
            return start_database()
        raise AssertionError("Unexpected fixture control")

    root = Path(__file__).resolve().parents[5]
    try:
        with study_assets(control, fixture_key) as origin, server(case, tmp_path, origins=(origin,)) as (client, process):
            result = subprocess.run(["node", str(root / "scripts/prove-study-learning.mjs")],
                input=json.dumps({"frontend": origin, "learning": str(client.base_url).rstrip("/"),
                    "credential": case.configuration.token, "scenario": scenario, "fixture_key": fixture_key}),
                cwd=root, capture_output=True, text=True, timeout=90)
            (tmp_path / f"study-proof-{scenario}.log").write_text(result.stdout + result.stderr)
            assert result.returncode == 0, (result.stdout + result.stderr)[-5000:]
    finally:
        if database_stopped:
            start_database()
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.review_attempt_receipts")[0][0] == 1
