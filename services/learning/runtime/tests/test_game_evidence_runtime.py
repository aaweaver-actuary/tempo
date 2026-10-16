"""Installed producer -> authenticated real HTTP -> independent Learning PG."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json, os, socket, subprocess, uuid

import httpx, psycopg, pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
from test_runtime import bundle
from test_restart import server


class Producer:
    def __init__(self, python, environment, owner, tmp_path):
        self.python, self.environment, self.owner, self.tmp_path = (
            python,
            environment,
            owner,
            tmp_path,
        )

    def run(self, *arguments):
        result = subprocess.run(
            [self.python, "-I", "-m", "tempo_game_evidence.cli", *map(str, arguments)],
            env=self.environment,
            cwd=self.tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
        return json.loads(result.stdout)

    def query(self, statement, parameters=()):
        with psycopg.connect(self.owner) as database:
            cursor = database.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else cursor.rowcount

    def load(self, payload, activation_id, receipt):
        definition = payload["publication"]["definitions"][0]
        snapshot = {
            "schema_version": 1,
            "snapshot_id": "actual-learning-activation",
            "revision": 1,
            "complete": True,
            "publication": payload["publication"],
            "activation": {
                "contract_version": 1,
                "workspace_id": "workspace-a",
                "activation_id": activation_id,
                "reason": None,
                **receipt,
            },
            "routes": [
                {
                    "repertoire_id": "repertoire-a",
                    "route_id": "accepted-route",
                    "learner_color": "white",
                    "starting_fen": definition["starting_fen"],
                    "moves": definition["specification"]["accepted_lines"][0],
                    "bindings": [
                        {"ply": 0, "card_id": definition["card_id"], "reentry": False}
                    ],
                }
            ],
        }
        snapshot_path = self.tmp_path / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot))
        self.run("load-snapshot", snapshot_path)
        pgn = self.tmp_path / "miss.pgn"
        pgn.write_text(
            '[Event "F1 owned fixture"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. Nf3 1-0\n'
        )
        return self.run(
            "import-pgn",
            pgn,
            "--workspace",
            "workspace-a",
            "--player",
            "Learner",
            "--source-id",
            "owned-game",
        )["game_id"]

    def work(self, endpoint):
        return self.run("work", "--max-jobs", "32", "--learning-endpoint", endpoint)


@pytest.fixture
def producer(case, tmp_path):
    python = os.environ.get("TEMPO_LEARNING_TEST_GAME_EVIDENCE_PYTHON")
    assert python and Path(python).is_file(), (
        "Owning runner must install the separate producer"
    )
    identity = "evidence_" + uuid.uuid4().hex[:12]
    owner, runtime = identity + "_owner", identity + "_runtime"
    owner_password, runtime_password = uuid.uuid4().hex, uuid.uuid4().hex
    admin = make_conninfo(case.admin, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as database:
        for name, password in [(owner, owner_password), (runtime, runtime_password)]:
            database.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(name), sql.Literal(password))
            )
        database.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(identity), sql.Identifier(owner)
            )
        )
        database.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(identity)
            )
        )
        database.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(identity), sql.Identifier(runtime)
            )
        )
    owner_url = make_conninfo(
        admin, dbname=identity, user=owner, password=owner_password
    )
    runtime_url = make_conninfo(
        admin, dbname=identity, user=runtime, password=runtime_password
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TEMPO_", "PG", "CELERY_", "PYTHON"))
    }
    token = tmp_path / "producer-learning-credential"
    token.write_text(case.configuration.evidence_token)
    token.chmod(0o600)
    environment.update(
        TEMPO_GAME_EVIDENCE_DATABASE_URL=runtime_url,
        TEMPO_GAME_EVIDENCE_MIGRATION_URL=owner_url,
        TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE=str(token),
    )
    instance = Producer(python, environment, owner_url, tmp_path)
    try:
        instance.run("migrate")
        environment.pop("TEMPO_GAME_EVIDENCE_MIGRATION_URL")
        with psycopg.connect(owner_url) as database:
            database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            database.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(runtime)
                )
            )
            database.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(runtime))
            )
            database.execute(
                sql.SQL(
                    "REVOKE INSERT,UPDATE,DELETE ON evidence_schema_migrations FROM {}"
                ).format(sql.Identifier(runtime))
            )
        yield instance
    finally:
        with psycopg.connect(admin, autocommit=True) as database:
            database.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(identity))
            )
            for role in [runtime, owner]:
                database.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def activate_without_queue_work(case, payload=None):
    payload = payload or bundle()
    headers = {"Authorization": "Bearer " + case.configuration.operator_token}
    assert (
        case.client.post(
            "/internal/publications/stage", json=payload, headers=headers
        ).status_code
        == 200
    )
    activation_id = uuid.uuid4().hex
    response = case.client.post(
        "/internal/publications/activate",
        json={
            "activation_id": activation_id,
            "publication_id": "publication-a",
            "publication_generation": 1,
            "expected_activation_revision": 0,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return payload, activation_id, response.json()


@contextmanager
def lose_acknowledgement(upstream):
    class ForwardAndDisconnect(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            response = httpx.post(
                upstream,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.headers["Authorization"],
                },
                timeout=5,
            )
            assert (
                response.status_code == 200 and response.json()["status"] == "completed"
            )
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *arguments):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ForwardAndDisconnect)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{proxy.server_port}/internal/evidence"
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)


def test_installed_producer_lost_acknowledgement_replays_after_learning_restart(
    case, producer, tmp_path
):
    payload, activation_id, receipt = activate_without_queue_work(case)
    producer.load(payload, activation_id, receipt)
    with server(case, tmp_path) as (client, process):
        endpoint = str(client.base_url).rstrip("/") + "/internal/evidence"
        with lose_acknowledgement(endpoint) as lossy:
            producer.work(lossy)
    assert producer.query("SELECT status FROM outbox")[0][0] == "retryable_failure"
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 1
    )
    producer.query("UPDATE outbox SET next_attempt_at=now()")
    with server(case, tmp_path) as (client, process):
        producer.work(str(client.base_url).rstrip("/") + "/internal/evidence")
        producer.work(str(client.base_url).rstrip("/") + "/internal/evidence")
    assert producer.query("SELECT status FROM outbox")[0][0] == "completed"
    assert producer.query("SELECT COUNT(*) FROM accepted_heads")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 1
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 1
    )
    parameters = conninfo_to_dict(case.configuration.database_url)
    evidence_database = conninfo_to_dict(
        producer.environment["TEMPO_GAME_EVIDENCE_DATABASE_URL"]
    )["dbname"]
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            make_conninfo(case.configuration.database_url, dbname=evidence_database)
        )
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            make_conninfo(
                producer.environment["TEMPO_GAME_EVIDENCE_DATABASE_URL"],
                dbname=parameters["dbname"],
            )
        )
    corrected = producer.tmp_path / "miss.pgn"
    corrected.write_text(
        '[Event "F1 owned fixture"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 1-0\n'
    )
    producer.run(
        "import-pgn",
        corrected,
        "--workspace",
        "workspace-a",
        "--player",
        "Learner",
        "--source-id",
        "owned-game",
    )
    with server(case, tmp_path) as (client, process):
        producer.work(str(client.base_url).rstrip("/") + "/internal/evidence")
    deliveries = producer.query(
        "SELECT payload,status FROM outbox ORDER BY evidence_revision"
    )
    assert len(deliveries) == 2 and all(row[1] == "completed" for row in deliveries)
    assert deliveries[1][0]["withdrawals"][0]["expected_obligation_revision"] == 1
    assert (
        case.query("SELECT active FROM learning.remediation_obligation_heads")[0][0]
        is False
    )
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0


def test_native_browser_settles_accepted_remediation_and_introduces_ordinary_work_with_producer_stopped(
    case, producer, tmp_path
):
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
            for move in ["e2e4", "d2d4"]
        ]
    )
    payload, activation_id, receipt = activate_without_queue_work(case, payload)
    producer.load(payload, activation_id, receipt)
    with server(case, tmp_path) as (client, process):
        producer.work(str(client.base_url).rstrip("/") + "/internal/evidence")
    # Both installed producer and Learning processes have exited. No optional
    # provider, broker, Content, Intelligence or model process is started.
    assert producer.query("SELECT status FROM outbox")[0][0] == "completed"
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 0
    script = Path(__file__).resolve().parents[1] / "browser/prove.mjs"
    with server(case, tmp_path) as (client, process):
        proof = subprocess.run(
            ["node", str(script)],
            input=json.dumps(
                {
                    "url": str(client.base_url).rstrip("/"),
                    "token": case.configuration.token,
                    "loss": True,
                    "remediation": True,
                }
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proof.returncode == 0, (proof.stdout + proof.stderr)[-6000:]
        result = json.loads(proof.stdout)
        assert result["complete"] and result["remediation"] and result["remaining"] >= 1
        from test_runtime import review_payload

        reinforcement_reviews = 0
        for _ in range(3):
            ordinary = client.get("/api/queue/window").json()["cards"][0]
            if ordinary["id"] == payload["publication"]["definitions"][1]["card_id"]:
                break
            # Keep first-correct reinforcement under the existing scheduler.
            assert ordinary["id"] == payload["publication"]["definitions"][0]["card_id"]
            assert ordinary["attempt_state"] == "reinforcement"
            response = client.post(
                f"/api/cards/{ordinary['id']}/review",
                json=review_payload(ordinary),
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            assert response.status_code == 200, response.text
            reinforcement_reviews += 1
        assert ordinary["id"] == payload["publication"]["definitions"][1]["card_id"]
        assert (
            ordinary["admission_kind"] == "new"
            and not ordinary["gameplay_priority_reason"]
        )
        original = review_payload(ordinary)
        operation = uuid.uuid4().hex
        saved = client.post(
            f"/api/cards/{ordinary['id']}/review",
            json=original,
            headers={"Idempotency-Key": operation},
        )
        assert saved.status_code == 200, saved.text
        assert (
            client.post(
                f"/api/cards/{ordinary['id']}/review",
                json=original,
                headers={"Idempotency-Key": operation},
            ).json()
            == saved.json()
        )
    assert (
        case.query("SELECT COUNT(*) FROM learning.reviews")[0][0]
        == 2 + reinforcement_reviews
    )
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        == 1
    )
