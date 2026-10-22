"""Real HTTP process lifecycle and task-owned PostgreSQL process restart."""

from contextlib import contextmanager
from pathlib import Path
import json, os, socket, subprocess, sys, time, uuid
import httpx, psycopg
from tempo_learning_runtime import gateway
from tempo_learning_database.connections import connection
from test_runtime import activate, head, review_payload


@contextmanager
def server(case, tmp_path, maintenance=False, origins=()):
    token = tmp_path / "credential"
    token.write_text(case.configuration.token)
    token.chmod(0o600)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TEMPO_DATABASE_", "TEMPO_LEARNING_", "CELERY_", "PG"))
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    environment.update(
        TEMPO_LEARNING_WORKSPACE=case.configuration.workspace_id,
        TEMPO_LEARNING_DATABASE_URL=case.configuration.database_url,
        TEMPO_LEARNING_READER_URL=case.configuration.reader_url,
        TEMPO_LEARNING_TOKEN_FILE=str(token),
        TEMPO_LEARNING_ORIGINS=",".join(["http://localhost:5173", f"http://127.0.0.1:{port}", *origins]),
        TEMPO_LEARNING_HOSTS="127.0.0.1",
        TEMPO_LEARNING_MAINTENANCE="1" if maintenance else "0",
        TEMPO_LEARNING_TABLEBASE_URL="",
    )
    if case.configuration.evidence_token:
        evidence_token = tmp_path / "evidence-credential"
        evidence_token.write_text(case.configuration.evidence_token)
        evidence_token.chmod(0o600)
        environment["TEMPO_LEARNING_EVIDENCE_TOKEN_FILE"] = str(evidence_token)
    if case.configuration.operator_token:
        operator_token = tmp_path / "operator-credential"
        operator_token.write_text(case.configuration.operator_token)
        operator_token.chmod(0o600)
        environment["TEMPO_LEARNING_OPERATOR_TOKEN_FILE"] = str(operator_token)
    output = (tmp_path / ("server-" + uuid.uuid4().hex + ".log")).open("w+")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-m",
            "tempo_learning_runtime.cli",
            "serve",
            "--port",
            str(port),
        ],
        env=environment,
        cwd=tmp_path,
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={
            "Authorization": "Bearer " + case.configuration.token,
            "Origin": "http://localhost:5173",
        },
        timeout=3,
    )
    def restart_service():
        nonlocal process
        arguments = process.args
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process = subprocess.Popen(arguments, env=environment, cwd=tmp_path,
                                   stdout=output, stderr=subprocess.STDOUT)
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("Owned Learning restart exited")
            try:
                if client.get("/health/ready").status_code == 200:
                    return {"restarted": True, "pid": process.pid}
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        raise AssertionError("Owned Learning restart did not become ready")
    process.restart_service = restart_service
    try:
        for _ in range(100):
            if process.poll() is not None:
                output.seek(0)
                raise AssertionError(output.read()[-2000:])
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Installed Learning HTTP process did not become ready")
        yield client, process
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        output.close()


def test_process_restart_replays_a_committed_review_after_response_loss(case, tmp_path):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    with server(case, tmp_path) as (client, process):
        # Consume no response body. PostgreSQL is authoritative after the process exits.
        request = client.build_request(
            "POST",
            f"/api/cards/{card['id']}/review",
            json=payload,
            headers={"Idempotency-Key": operation},
        )
        response = client.send(request, stream=True)
        assert response.status_code == 200
        response.close()
        process.terminate()
        process.wait(timeout=5)
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    with server(case, tmp_path) as (client, process):
        retried = client.post(
            f"/api/cards/{card['id']}/review",
            json=payload,
            headers={"Idempotency-Key": operation},
        )
        assert retried.status_code == 200, retried.text
        receipt = client.get("/api/operations/" + operation).json()
        assert receipt["response"] == retried.json()
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_restart_resumes_durable_foreground_intent_without_a_broker(case, tmp_path):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    with case.database.scope():
        gateway.record_intent(
            operation, "cards.review", {"card_id": card["id"], "review": payload}
        )
    with server(case, tmp_path, maintenance=True) as (client, process):
        for _ in range(60):
            receipt = client.get("/api/operations/" + operation).json()
            if receipt["state"] == "complete":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Essential intent did not resume")
        assert receipt["response"]["persisted"] is True
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1


def test_owned_database_restart_exposes_unready_then_recovers_existing_receipts(
    case, tmp_path
):
    activate(case)
    card = head(case)
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    identity = os.environ["TEMPO_LEARNING_TEST_FIXTURE"]
    labels = json.loads(
        subprocess.check_output(
            ["docker", "inspect", identity, "--format", "{{json .Config.Labels}}"],
            text=True,
        )
    )
    assert (
        labels.get("tempo.owner") == identity
        and labels.get("tempo.disposable") == "true"
    )
    with server(case, tmp_path) as (client, process):
        saved = client.post(
            f"/api/cards/{card['id']}/review",
            json=payload,
            headers={"Idempotency-Key": operation},
        )
        assert saved.status_code == 200, saved.text
        stopped = False
        try:
            subprocess.run(
                ["docker", "stop", "--time", "3", identity],
                check=True,
                capture_output=True,
                text=True,
            )
            stopped = True
            unready = client.get("/health/ready")
            assert unready.status_code == 503
            assert unready.json() == {
                "status": "unavailable",
                "domain": "database",
                "reason": "connection_unavailable",
            }
            assert client.get("/health/live").status_code == 200
        finally:
            if stopped:
                subprocess.run(
                    ["docker", "start", identity],
                    check=True,
                    capture_output=True,
                    text=True,
                )
        for _ in range(60):
            if client.get("/health/ready").status_code == 200:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "Learning did not recover after its owned database restarted"
            )
        replayed = client.post(
            f"/api/cards/{card['id']}/review",
            json=payload,
            headers={"Idempotency-Key": operation},
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == saved.json()


def test_fresh_process_resumes_saved_queue_slice_cursor(case, tmp_path):
    from test_runtime import bundle
    from test_publications import stage, activate_generation
    from tempo_learning_runtime.maintenance import run_once

    payload = bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [[move]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
            for move in ("e2e4", "d2d4")
        ]
    )
    assert stage(case, payload).status_code == 200
    assert activate_generation(case, 1).status_code == 200
    with case.database.scope():
        assert run_once() is True
    checkpoint = case.query("SELECT payload_json FROM learning.background_tasks")[0][0]
    assert json.loads(checkpoint)["after_order_key"]
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 1
    with server(case, tmp_path, maintenance=True) as (client, process):
        for _ in range(80):
            if (
                case.query("SELECT state FROM learning.background_tasks")[0][0]
                == "complete"
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Fresh process did not resume its saved queue cursor")
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 2
    assert (
        case.query(
            "SELECT COUNT(DISTINCT card_id) FROM learning.queue_attempt_origins"
        )[0][0]
        == 2
    )
