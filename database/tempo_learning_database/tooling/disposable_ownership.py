"""Create and validate a trusted local receipt for disposable Study PostgreSQL."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


_RECEIPT_NAME = "ownership.json"
_RECEIPT_KEYS = {
    "schema_version",
    "invocation_id",
    "project_name",
    "database",
    "container_id",
    "volume_name",
    "network_id",
    "network_name",
    "service",
    "host",
    "port",
    "system_identifier",
}
_INVOCATION_ID = re.compile(r"^[0-9a-f]{16,32}$")
_RESOURCE_ID = re.compile(r"^[0-9a-f]{64}$")
_COMPOSE_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_SYSTEM_IDENTIFIER = re.compile(r"^[0-9]+$")
_DATABASE = re.compile(r"^learning_study_test_([0-9a-f]{16,32})$")
_MAX_RECEIPT_BYTES = 4096
_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 30


class OwnershipError(Exception):
    """A safe-to-report ownership receipt failure."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise OwnershipError(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _absolute_path(path):
    try:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            _fail("receipt_path_invalid")
        candidate = Path(raw)
        if not candidate.is_absolute() or ".." in candidate.parts:
            _fail("receipt_path_invalid")
        return candidate
    except OwnershipError:
        raise
    except Exception:
        _fail("receipt_path_invalid")


def _read_private_receipt(path):
    candidate = _absolute_path(path)
    try:
        directory = candidate.parent
        directory_info = directory.lstat()
        if not stat.S_ISDIR(directory_info.st_mode):
            _fail("receipt_path_invalid")
        if stat.S_IMODE(directory_info.st_mode) != 0o700:
            _fail("receipt_directory_permissions")

        file_info = candidate.lstat()
        if not stat.S_ISREG(file_info.st_mode):
            _fail("receipt_path_invalid")
        if stat.S_IMODE(file_info.st_mode) != 0o600:
            _fail("receipt_file_permissions")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(candidate, flags)
        try:
            opened_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_info.st_mode)
                or opened_info.st_dev != file_info.st_dev
                or opened_info.st_ino != file_info.st_ino
                or stat.S_IMODE(opened_info.st_mode) != 0o600
            ):
                _fail("receipt_path_invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                contents = stream.read(_MAX_RECEIPT_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(contents) > _MAX_RECEIPT_BYTES:
            _fail("receipt_too_large")
        return json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_object)
    except OwnershipError:
        raise
    except FileNotFoundError:
        _fail("receipt_unavailable")
    except Exception:
        _fail("receipt_unavailable")


def _validate_receipt(receipt, *, database, path):
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        _fail("receipt_schema_invalid")
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        _fail("receipt_schema_invalid")
    if type(receipt["port"]) is not int or receipt["port"] != 5432:
        _fail("receipt_schema_invalid")
    for field in (
        "invocation_id",
        "project_name",
        "database",
        "container_id",
        "volume_name",
        "network_id",
        "network_name",
        "service",
        "host",
        "system_identifier",
    ):
        if not isinstance(receipt[field], str):
            _fail("receipt_schema_invalid")

    invocation_id = receipt["invocation_id"]
    if not _INVOCATION_ID.fullmatch(invocation_id):
        _fail("receipt_schema_invalid")
    expected_project = "tempo-study-test-" + invocation_id
    expected_database = "learning_study_test_" + invocation_id
    if (
        receipt["project_name"] != expected_project
        or receipt["database"] != expected_database
        or database != expected_database
        or receipt["volume_name"] != expected_project + "_postgres-data"
        or receipt["network_name"] != expected_project + "_default"
        or receipt["service"] != "postgres"
        or receipt["host"] != "postgres"
    ):
        _fail("receipt_identity_mismatch")
    if path.parent.name != invocation_id:
        _fail("receipt_path_invalid")

    for field in ("container_id", "network_id"):
        value = receipt[field]
        if not isinstance(value, str) or not _RESOURCE_ID.fullmatch(value):
            _fail("receipt_schema_invalid")
    system_identifier = receipt["system_identifier"]
    if not _SYSTEM_IDENTIFIER.fullmatch(system_identifier):
        _fail("receipt_schema_invalid")
    return receipt


def load_ownership_receipt(path, *, database):
    """Load the exact private ownership receipt for one bootstrap database."""
    candidate = _absolute_path(path)
    receipt = _read_private_receipt(candidate)
    return _validate_receipt(receipt, database=database, path=candidate)


def _validate_identity(invocation_id, database):
    if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(invocation_id):
        _fail("invocation_id_invalid")
    match = _DATABASE.fullmatch(database) if isinstance(database, str) else None
    if match is None or match.group(1) != invocation_id:
        _fail("database_invocation_mismatch")
    project_name = "tempo-study-test-" + invocation_id
    return {
        "invocation_id": invocation_id,
        "project_name": project_name,
        "database": database,
        "volume_name": project_name + "_postgres-data",
        "network_name": project_name + "_default",
    }


def _run(arguments, timeout):
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _fail("ownership_command_timeout")
    except Exception:
        _fail("ownership_command_failed")
    if result.returncode != 0 or not isinstance(result.stdout, str):
        _fail("ownership_command_failed")
    return result.stdout


def _json_output(arguments, timeout):
    output = _run(arguments, timeout)
    try:
        return json.loads(output, object_pairs_hook=_unique_object)
    except Exception:
        _fail("ownership_inspect_invalid")


def _labels(resource):
    value = resource.get("Labels")
    return value if isinstance(value, dict) else {}


def _require_labels(labels, *, project, compose_kind=None):
    if not isinstance(labels, dict) or (
        labels.get("tempo.owner") != project
        or labels.get("tempo.disposable") != "true"
        or labels.get("com.docker.compose.project") != project
    ):
        _fail("ownership_labels_mismatch")
    if compose_kind is not None and labels.get(compose_kind[0]) != compose_kind[1]:
        _fail("ownership_labels_mismatch")


def _single_inspect(arguments, timeout):
    value = _json_output(arguments, timeout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        _fail("ownership_inspect_invalid")
    return value[0]


def _timeout_value(timeout):
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= _MAX_TIMEOUT_SECONDS
    ):
        _fail("ownership_timeout_invalid")
    return timeout


def _private_env_file(path):
    try:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            _fail("env_file_invalid")
        candidate = Path(raw)
        if not candidate.is_absolute() or ".." in candidate.parts:
            _fail("env_file_invalid")
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode):
            _fail("env_file_invalid")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077 or not mode & 0o400:
            _fail("env_file_permissions")
        return candidate
    except OwnershipError:
        raise
    except Exception:
        _fail("env_file_invalid")


def _prepare_invocation_directory(path, invocation_id):
    candidate = _absolute_path(path)
    if candidate.name != invocation_id:
        _fail("invocation_directory_invalid")
    parent = candidate.parent
    try:
        parent_info = parent.lstat()
        if not stat.S_ISDIR(parent_info.st_mode):
            _fail("invocation_directory_invalid")
        created = False
        try:
            candidate.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        if created:
            os.chmod(candidate, 0o700)
        directory_info = candidate.lstat()
        if not stat.S_ISDIR(directory_info.st_mode):
            _fail("invocation_directory_invalid")
        if stat.S_IMODE(directory_info.st_mode) != 0o700:
            _fail("invocation_directory_permissions")
        return candidate
    except OwnershipError:
        raise
    except Exception:
        _fail("invocation_directory_invalid")


def _inspect_and_attest(*, compose_file, env_file, identity, timeout):
    compose_path = _absolute_path(compose_file)
    try:
        compose_info = compose_path.lstat()
        if not stat.S_ISREG(compose_info.st_mode):
            _fail("compose_file_invalid")
    except OwnershipError:
        raise
    except Exception:
        _fail("compose_file_invalid")

    project = identity["project_name"]
    compose_ps = [
        "docker",
        "compose",
        "--profile",
        "bootstrap",
        "--env-file",
        str(env_file),
        "--project-name",
        project,
        "--file",
        str(compose_path),
        "ps",
        "--status",
        "running",
        "--quiet",
        "postgres",
    ]
    container_refs = [line.strip() for line in _run(compose_ps, timeout).splitlines() if line.strip()]
    if len(container_refs) != 1 or not _COMPOSE_CONTAINER_ID.fullmatch(container_refs[0]):
        _fail("ownership_container_mismatch")

    container = _single_inspect(
        ["docker", "inspect", "--type=container", container_refs[0]], timeout
    )
    container_id = container.get("Id")
    if not isinstance(container_id, str) or not _RESOURCE_ID.fullmatch(container_id):
        _fail("ownership_container_mismatch")
    if not container_id.startswith(container_refs[0]):
        _fail("ownership_container_mismatch")
    config = container.get("Config")
    if not isinstance(config, dict):
        _fail("ownership_inspect_invalid")
    container_labels = config.get("Labels")
    _require_labels(
        container_labels,
        project=project,
        compose_kind=("com.docker.compose.service", "postgres"),
    )
    state = container.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        _fail("ownership_container_mismatch")

    mounts = container.get("Mounts")
    if not isinstance(mounts, list):
        _fail("ownership_mount_mismatch")
    postgres_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("Destination") == "/var/lib/postgresql"
    ]
    if (
        len(postgres_mounts) != 1
        or postgres_mounts[0].get("Type") != "volume"
        or postgres_mounts[0].get("Name") != identity["volume_name"]
    ):
        _fail("ownership_mount_mismatch")

    volume = _single_inspect(
        ["docker", "volume", "inspect", identity["volume_name"]], timeout
    )
    if volume.get("Name") != identity["volume_name"]:
        _fail("ownership_volume_mismatch")
    _require_labels(
        _labels(volume),
        project=project,
        compose_kind=("com.docker.compose.volume", "postgres-data"),
    )

    network = _single_inspect(
        ["docker", "network", "inspect", identity["network_name"]], timeout
    )
    if network.get("Name") != identity["network_name"]:
        _fail("ownership_network_mismatch")
    network_id = network.get("Id")
    if not isinstance(network_id, str) or not _RESOURCE_ID.fullmatch(network_id):
        _fail("ownership_network_mismatch")
    _require_labels(
        _labels(network),
        project=project,
        compose_kind=("com.docker.compose.network", "default"),
    )

    network_settings = container.get("NetworkSettings")
    if not isinstance(network_settings, dict):
        _fail("ownership_network_mismatch")
    port_map = network_settings.get("Ports") or {}
    if not isinstance(port_map, dict):
        _fail("ownership_published_port")
    published = port_map.get("5432/tcp")
    if published not in (None, []):
        _fail("ownership_published_port")

    container_networks = network_settings.get("Networks") or {}
    if not isinstance(container_networks, dict):
        _fail("ownership_network_mismatch")
    endpoint = container_networks.get(identity["network_name"])
    if (
        not isinstance(endpoint, dict)
        or endpoint.get("NetworkID") != network_id
        or not isinstance(endpoint.get("Aliases"), list)
        or "postgres" not in endpoint["Aliases"]
    ):
        _fail("ownership_network_mismatch")
    network_containers = network.get("Containers")
    if not isinstance(network_containers, dict) or container_id not in network_containers:
        _fail("ownership_network_mismatch")

    control_data = _run(
        [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            'pg_controldata "$PGDATA"',
        ],
        timeout,
    )
    identifiers = []
    for line in control_data.splitlines():
        match = re.fullmatch(r"\s*Database system identifier:\s*([0-9]+)\s*", line)
        if match:
            identifiers.append(match.group(1))
    if len(identifiers) != 1:
        _fail("ownership_system_identifier_invalid")

    return {
        "schema_version": 1,
        **identity,
        "container_id": container_id,
        "network_id": network_id,
        "service": "postgres",
        "host": "postgres",
        "port": 5432,
        "system_identifier": identifiers[0],
    }


def _write_receipt(path, receipt):
    if path.name != _RECEIPT_NAME:
        _fail("receipt_path_invalid")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    identity = None
    write_succeeded = False
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            _fail("receipt_file_permissions")
        payload = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        write_succeeded = True
    except FileExistsError:
        _fail("receipt_would_overwrite")
    except OwnershipError:
        raise
    except Exception:
        _fail("receipt_write_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None and not write_succeeded:
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    path.unlink()
            except OSError:
                pass
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass
    return identity


def produce_ownership_receipt(
    *, compose_file, env_file, invocation_directory, invocation_id, database, timeout=10
):
    """Attest one running Compose PostgreSQL service and create its private receipt."""
    timeout = _timeout_value(timeout)
    identity = _validate_identity(invocation_id, database)
    env_file = _private_env_file(env_file)
    directory = _prepare_invocation_directory(invocation_directory, invocation_id)
    path = directory / _RECEIPT_NAME
    if os.path.lexists(path):
        _fail("receipt_would_overwrite")
    receipt = _inspect_and_attest(
        compose_file=compose_file,
        env_file=env_file,
        identity=identity,
        timeout=timeout,
    )
    _write_receipt(path, receipt)
    return receipt


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--invocation-directory", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        produce_ownership_receipt(
            compose_file=arguments.compose_file,
            env_file=arguments.env_file,
            invocation_directory=arguments.invocation_directory,
            invocation_id=arguments.invocation_id,
            database=arguments.database,
            timeout=arguments.timeout,
        )
    except OwnershipError as error:
        print(error.code, file=sys.stderr)
        return 2
    print(str(Path(arguments.invocation_directory) / _RECEIPT_NAME))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
