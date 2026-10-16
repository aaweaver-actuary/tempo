"""Actual installed server start, private registration, and process restart."""

from contextlib import contextmanager
import json
import os
import signal
import socket
import subprocess
import sys
from threading import Thread
import time
from uuid import uuid4

import httpx
from psycopg.conninfo import conninfo_to_dict

from .support import request_value
from .test_provider_postgres import fake_provider


_CHILD = r'''
import ipaddress
import socket
import sys

def local(host):
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_getaddrinfo = socket.getaddrinfo

def connect(connection, address):
    if isinstance(address, tuple) and not local(address[0]):
        raise RuntimeError("external_network_disabled")
    return original_connect(connection, address)

def connect_ex(connection, address):
    if isinstance(address, tuple) and not local(address[0]):
        raise RuntimeError("external_network_disabled")
    return original_connect_ex(connection, address)

def getaddrinfo(host, *arguments, **keywords):
    if not local(host):
        raise RuntimeError("external_network_disabled")
    return original_getaddrinfo(host, *arguments, **keywords)

socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo

def forbidden_connect(use_connect_ex):
    with socket.socket() as connection:
        operation = connection.connect_ex if use_connect_ex else connection.connect
        operation(("192.0.2.1", 443))

# Prove all guards reject before any address resolution or connection attempt.
for operation in (lambda: socket.getaddrinfo("external.invalid", 443),
                  lambda: forbidden_connect(False),
                  lambda: forbidden_connect(True)):
    try:
        operation()
    except RuntimeError as error:
        assert str(error) == "external_network_disabled"
    else:
        raise RuntimeError("external_network_guard_missing")
print("installed_loopback_guards_ready", flush=True)
from tempo_intelligence.cli import main
raise SystemExit(main(["serve", "--host", "127.0.0.1", "--port", sys.argv[1]]))
'''


def unused_loopback_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


@contextmanager
def installed_server(environment, private_directory, secret_values):
    port = unused_loopback_port()
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _CHILD, str(port)],
        env=environment, cwd=private_directory,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = []

    def drain(name, stream):
        while True:
            data = stream.read(1024)
            if not data:
                break
            remaining = 65536 - len(captured[name])
            captured[name].extend(data[:max(0, remaining)])
            if len(data) > remaining and name not in overflow:
                overflow.append(name)

    drains = [Thread(target=drain, args=(name, stream), daemon=True)
              for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in drains:
        thread.start()
    try:
        yield process, f"http://127.0.0.1:{port}"
    finally:
        failed = sys.exc_info()[0] is not None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for thread in drains:
            thread.join(timeout=3)
        for stream in (process.stdout, process.stderr):
            stream.close()
        text = (captured["stdout"] + captured["stderr"]).decode("utf-8", errors="replace")
        if any(value and value in text for value in secret_values):
            raise AssertionError("installed_server_exposed_private_material")
        if overflow:
            raise AssertionError("installed_server_diagnostic_limit_exceeded")
        if not failed:
            # Uvicorn restores/re-raises SIGTERM after completing its lifespan.
            assert process.returncode in (0, -signal.SIGTERM), "installed_server_did_not_stop_cleanly"
            assert "Application shutdown complete." in text
            assert "installed_loopback_guards_ready" in text
            assert all(not thread.is_alive() for thread in drains)


def wait_for_health(client, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("installed_server_exited_before_ready")
        try:
            response = client.get("/healthz")
            if response.status_code == 200:
                assert response.json() == {"status": "ready", "workspace_id": "fixture"}
                return
        except httpx.TransportError:
            pass
        time.sleep(0.025)
    raise AssertionError("installed_server_readiness_timeout")


def wait_for_source(client, process, state):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("installed_server_stopped_during_request")
        response = client.get("/v1/requests/request")
        assert response.status_code == 200
        source = response.json()["sources"][0]
        if source["state"] == state:
            return source
        time.sleep(0.025)
    raise AssertionError("installed_server_source_progress_timeout")


def test_installed_server_missing_credentials_explicit_reload_counts_and_restart(state):
    _, configuration, credential_path = state
    private_directory = credential_path.parent
    private_files = {
        "DATABASE_URL": configuration.database_url,
        "CALLER_TOKEN": configuration.caller_token,
        "OPERATOR_TOKEN": configuration.operator_token,
        "WORKER_TOKEN": configuration.worker_token,
    }
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PG", "TEMPO_", "PYTHON", "VIRTUAL_ENV", "PYTEST_"))}
    for name, material in private_files.items():
        path = private_directory / (name.lower() + ".txt")
        path.write_text(material)
        path.chmod(0o600)
        environment["TEMPO_INTELLIGENCE_" + name + "_FILE"] = str(path)
    environment.update(
        TEMPO_INTELLIGENCE_WORKSPACE="fixture",
        TEMPO_INTELLIGENCE_CREDENTIAL_FILE=str(credential_path),
        TEMPO_INTELLIGENCE_WORKER_ENABLED="1",
        TEMPO_INTELLIGENCE_POLL_INTERVAL="0.05",
    )
    caller = {"Authorization": "Bearer " + configuration.caller_token,
              "X-Tempo-Workspace": "fixture"}
    operator = {"Authorization": "Bearer " + configuration.operator_token,
                "X-Tempo-Workspace": "fixture"}
    provider_token = "synthetic_process_provider_credential"
    secrets = [*private_files.values(), provider_token,
               conninfo_to_dict(configuration.database_url).get("password", "")]
    with fake_provider() as (provider_url, provider_requests):
        environment["TEMPO_INTELLIGENCE_PROVIDER_BASE_URL"] = provider_url
        with installed_server(environment, private_directory, secrets) as (process, url):
            with httpx.Client(base_url=url, headers=caller, timeout=1, trust_env=False) as client:
                wait_for_health(client, process)
                submitted = client.post("/v1/requests", json=request_value())
                assert submitted.status_code == 200
                missing = wait_for_source(client, process, "waiting_credentials")
                assert missing["error_code"] == "credential_missing"
                assert missing["credential"]["status"] == "credential_missing"
                assert missing["evidence_id"] is None and provider_requests == []
                credential_path.write_text(json.dumps({
                    "schema_version": 1, "registration_id": str(uuid4()), "token": provider_token,
                }))
                credential_path.chmod(0o600)
                reloaded = client.post(
                    "/v1/credentials/lichess-explorer/reload", headers=operator,
                    json={"expected_generation": 0},
                )
                assert reloaded.status_code == 200
                assert reloaded.json()["generation"] == 1
                accepted = wait_for_source(client, process, "complete")
                evidence_id = accepted["evidence_id"]
                evidence = client.get("/v1/evidence/" + evidence_id)
                assert evidence.status_code == 200
                retained = evidence.json()
                assert retained["payload"]["total_games"] == 10
                assert len(retained["payload"]["legal_observations"]) == 20
                assert len(provider_requests) == 1
        # A new interpreter and pool restore the matching private registration;
        # reads and durable accepted IDs need no repeat source request.
        with installed_server(environment, private_directory, secrets) as (process, url):
            with httpx.Client(base_url=url, headers=caller, timeout=1, trust_env=False) as client:
                wait_for_health(client, process)
                restored = client.get("/v1/requests/request")
                assert restored.status_code == 200
                source = restored.json()["sources"][0]
                assert source["state"] == "complete" and source["evidence_id"] == evidence_id
                assert source["credential"]["status"] == "accepted"
                assert client.get("/v1/evidence/" + evidence_id).json() == retained
                assert len(provider_requests) == 1
