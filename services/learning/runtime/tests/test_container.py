"""Final minimal image, restricted user, real HTTP review and PostgreSQL receipts."""

from pathlib import Path
import json, os, socket, subprocess, time, uuid
import httpx
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_runtime import activate, review_payload


def test_minimal_installed_container_load_issue_review_without_backend_or_brokers(
    case, tmp_path
):
    activate(case)
    identity = os.environ["TEMPO_LEARNING_TEST_FIXTURE"]
    image = os.environ["TEMPO_LEARNING_TEST_IMAGE"]
    name = identity + "-api-" + uuid.uuid4().hex[:6]
    token = tmp_path / "container-credential"
    token.write_text(case.configuration.token)
    token.chmod(0o444)
    tmp_path.chmod(0o755)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    environment = dict(os.environ)
    environment.update(
        TEMPO_LEARNING_WORKSPACE=case.configuration.workspace_id,
        TEMPO_LEARNING_DATABASE_URL=make_conninfo(
            case.configuration.database_url, host="host.docker.internal"
        ),
        TEMPO_LEARNING_READER_URL=make_conninfo(
            case.configuration.reader_url, host="host.docker.internal"
        ),
        TEMPO_LEARNING_TOKEN_FILE="/run/credential",
        TEMPO_LEARNING_ORIGINS="http://localhost:5173",
        TEMPO_LEARNING_HOSTS="127.0.0.1",
        TEMPO_LEARNING_MAINTENANCE="0",
        TEMPO_LEARNING_TABLEBASE_URL="",
    )
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        "tempo.owner=" + identity,
        "--label",
        "tempo.disposable=true",
        "--read-only",
        "--tmpfs",
        "/tmp:rw",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--publish",
        f"127.0.0.1:{port}:8001",
        "--mount",
        f"type=bind,source={token},target=/run/credential,readonly",
    ]
    for key in (
        "WORKSPACE",
        "DATABASE_URL",
        "READER_URL",
        "TOKEN_FILE",
        "ORIGINS",
        "HOSTS",
        "MAINTENANCE",
        "TABLEBASE_URL",
    ):
        command += ["--env", "TEMPO_LEARNING_" + key]
    try:
        subprocess.run(
            [*command, image],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={
                "Authorization": "Bearer " + case.configuration.token,
                "Origin": "http://localhost:5173",
            },
            timeout=2,
        ) as client:
            for _ in range(80):
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError(
                    subprocess.check_output(["docker", "logs", name], text=True)[-3000:]
                )
            queue = client.get("/api/queue/window")
            assert queue.status_code == 200, queue.text
            card = queue.json()["cards"][0]
            operation = uuid.uuid4().hex
            payload = review_payload(card)
            saved = client.post(
                f"/api/cards/{card['id']}/review",
                json=payload,
                headers={"Idempotency-Key": operation},
            )
            assert saved.status_code == 200, saved.text
            assert (
                client.post(
                    f"/api/cards/{card['id']}/review",
                    json=payload,
                    headers={"Idempotency-Key": operation},
                ).json()
                == saved.json()
            )
        guard = "import importlib.util,os,pathlib;assert os.getuid()==10001;assert all(importlib.util.find_spec(name) is None for name in ('app','celery','redis','torch','onnxruntime','tempo_game_evidence'));assert not pathlib.Path('/build').exists();import tempo_learning_runtime as runtime;assert 'site-packages' in runtime.__file__"
        subprocess.run(
            ["docker", "exec", name, "/opt/learning/bin/python", "-I", "-c", guard],
            check=True,
            capture_output=True,
            text=True,
        )
        assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    finally:
        existing = subprocess.run(
            ["docker", "inspect", name, "--format", "{{json .Config.Labels}}"],
            capture_output=True,
            text=True,
        )
        if existing.returncode == 0:
            labels = json.loads(existing.stdout)
            assert (
                labels.get("tempo.owner") == identity
                and labels.get("tempo.disposable") == "true"
            )
            subprocess.run(
                ["docker", "rm", "--force", name], check=True, capture_output=True
            )
