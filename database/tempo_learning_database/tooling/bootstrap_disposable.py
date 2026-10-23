"""Explicitly bootstrap a Learning database on an owned disposable server."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo


_DATABASE_NAME = re.compile(r"^learning_study_test_[0-9a-f]{12,32}$")
_RUNNER_ID = re.compile(r"^tempo-learning-[0-9a-f]{16}$")
_OUTPUT_FILES = (
    "connection.json",
    "runtime.pgpass",
    "study.token",
    "operator.token",
)
_LOCK_FILE = ".bootstrap.lock"
_OWNER_STAGES = {"provision", "migrate", "grant_runtime"}


class BootstrapError(Exception):
    """A safe-to-report bootstrap failure with no driver exception text."""

    def __init__(self, code, stage="preflight", targets=None):
        super().__init__(code)
        self.code = code
        self.stage = stage
        self.targets = targets


def _load_driver():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    return psycopg, conninfo_to_dict, make_conninfo


def _load_owner_apis():
    database_source = str(Path(__file__).resolve().parents[2])
    if database_source not in sys.path:
        sys.path.insert(0, database_source)
    from tempo_learning_database.ddl import migrate

    return migrate.provision, migrate.migrate, migrate.grant_runtime


def _generate_secret():
    return secrets.token_urlsafe(32)


def _read_admin_url(path):
    try:
        candidate = Path(path)
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise BootstrapError("admin_url_file_permissions")
        value = candidate.read_text(encoding="utf-8").strip()
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("admin_url_file_unavailable") from None
    if not value or "\n" in value or "\r" in value:
        raise BootstrapError("admin_url_file_invalid")
    return value


def _docker_json(arguments):
    try:
        result = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BootstrapError("runner_ownership_unverified")
        return json.loads(result.stdout)
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("runner_ownership_unverified") from None


def _inspect_owned_server(identity):
    containers = _docker_json(["inspect", "--type=container", identity])
    volumes = _docker_json(["volume", "inspect", identity + "-data"])
    if not isinstance(containers, list) or len(containers) != 1:
        raise BootstrapError("runner_ownership_unverified")
    if not isinstance(volumes, list) or len(volumes) != 1:
        raise BootstrapError("runner_ownership_unverified")
    return containers[0], volumes[0]


def _verify_runner_ownership(admin_url, conninfo_to_dict, environment=None):
    """Bind runner-provided identity and admin URL to its labeled Docker resources."""
    environment = os.environ if environment is None else environment
    identity = environment.get("TEMPO_LEARNING_TEST_FIXTURE", "")
    runner_admin = environment.get("TEMPO_LEARNING_TEST_ADMIN", "")
    if not _RUNNER_ID.fullmatch(identity) or not runner_admin:
        raise BootstrapError("runner_ownership_missing")
    if runner_admin != admin_url:
        raise BootstrapError("runner_admin_url_mismatch")

    try:
        details = conninfo_to_dict(admin_url)
    except Exception:
        raise BootstrapError("admin_url_invalid") from None
    host = details.get("host")
    port = details.get("port")
    if (
        host != "127.0.0.1"
        or not isinstance(port, str)
        or not port.isdecimal()
        or not 1 <= int(port) <= 65535
        or details.get("dbname") != "postgres"
        or details.get("user") != "postgres"
    ):
        raise BootstrapError("runner_admin_url_invalid")

    container, volume = _inspect_owned_server(identity)
    container_labels = (container.get("Config") or {}).get("Labels") or {}
    volume_labels = volume.get("Labels") or {}
    if (
        container.get("Name") != "/" + identity
        or container_labels.get("tempo.owner") != identity
        or container_labels.get("tempo.disposable") != "true"
        or (container.get("State") or {}).get("Running") is not True
        or volume.get("Name") != identity + "-data"
        or volume_labels.get("tempo.owner") != identity
        or volume_labels.get("tempo.disposable") != "true"
    ):
        raise BootstrapError("runner_ownership_unverified")

    listeners = ((container.get("NetworkSettings") or {}).get("Ports") or {}).get(
        "5432/tcp"
    ) or []
    if len(listeners) != 1 or listeners[0].get("HostIp") != host:
        raise BootstrapError("runner_listener_unverified")
    if listeners[0].get("HostPort") != port:
        raise BootstrapError("runner_admin_port_mismatch")

    mounts = container.get("Mounts") or []
    if not any(
        mount.get("Type") == "volume"
        and mount.get("Name") == identity + "-data"
        and mount.get("Destination") == "/var/lib/postgresql"
        for mount in mounts
    ):
        raise BootstrapError("runner_volume_unverified")
    return host, port


def _validate_inputs(database, workspace, timezone_name):
    if not _DATABASE_NAME.fullmatch(database):
        raise BootstrapError("database_name_invalid")
    if not workspace or len(workspace) > 200 or any(ord(char) < 32 for char in workspace):
        raise BootstrapError("workspace_invalid")
    try:
        ZoneInfo(timezone_name)
    except Exception:
        raise BootstrapError("timezone_invalid") from None
    roles = {
        "owner": database + "_owner",
        "writer": database + "_writer",
        "reader": database + "_reader",
    }
    if any(len(role) > 63 for role in roles.values()):
        raise BootstrapError("role_name_invalid")
    return roles


@contextmanager
def _claimed_output_directory(path):
    directory = Path(path)
    created = False
    lock_fd = None
    lock_identity = None
    claim = {"path": directory, "keep": False}
    try:
        try:
            info = directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
            info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise BootstrapError("output_directory_invalid")
        if any(directory.iterdir()):
            raise BootstrapError("output_directory_not_empty")
        os.chmod(directory, 0o700)
        try:
            lock_fd = os.open(
                directory / _LOCK_FILE,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except Exception:
            raise BootstrapError("output_directory_claim_failed") from None
        lock_identity = os.fstat(lock_fd)
        os.write(lock_fd, (str(os.getpid()) + "\n").encode("ascii"))
        os.fsync(lock_fd)
        if {entry.name for entry in directory.iterdir()} != {_LOCK_FILE}:
            raise BootstrapError("output_directory_not_empty")
        yield claim
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("output_directory_unavailable") from None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
            lock_path = directory / _LOCK_FILE
            try:
                current = lock_path.lstat()
                if (
                    lock_identity is not None
                    and current.st_dev == lock_identity.st_dev
                    and current.st_ino == lock_identity.st_ino
                ):
                    lock_path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
        if created and not claim["keep"]:
            try:
                directory.rmdir()
            except OSError:
                pass


def _assert_targets_absent(psycopg, admin_url, database, roles):
    try:
        with psycopg.connect(admin_url) as connection:
            database_exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone()
            existing_roles = connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                ([roles["owner"], roles["writer"], roles["reader"]],),
            ).fetchall()
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("database_preflight_failed") from None
    if database_exists is not None or existing_roles:
        raise BootstrapError("target_already_exists")


def _pgpass_escape(value):
    return str(value).replace("\\", "\\\\").replace(":", "\\:")


def _write_private_outputs(directory, payloads):
    temporary_files = []
    installed_files = []
    try:
        if {entry.name for entry in directory.iterdir()} != {_LOCK_FILE}:
            raise BootstrapError("output_directory_changed", stage="outputs")
        for name, content in payloads:
            if name not in _OUTPUT_FILES:
                raise BootstrapError("output_contract_invalid", stage="outputs")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".bootstrap-", dir=directory
            )
            temporary = Path(temporary_name)
            temporary_files.append(temporary)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise

        for name, _content in payloads:
            destination = directory / name
            if os.path.lexists(destination):
                raise BootstrapError("output_would_overwrite", stage="outputs")
            temporary = temporary_files.pop(0)
            info = temporary.lstat()
            os.rename(temporary, destination)
            installed_files.append((destination, info.st_dev, info.st_ino))

        expected = set(_OUTPUT_FILES) | {_LOCK_FILE}
        if {entry.name for entry in directory.iterdir()} != expected:
            raise BootstrapError("output_contract_invalid", stage="outputs")
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
        finally:
            os.close(directory_fd)
    except BootstrapError:
        _remove_owned_outputs(temporary_files, installed_files)
        raise
    except Exception:
        _remove_owned_outputs(temporary_files, installed_files)
        raise BootstrapError("output_write_failed", stage="outputs") from None
    except BaseException:
        _remove_owned_outputs(temporary_files, installed_files)
        raise


def _remove_owned_outputs(temporary_files, installed_files):
    for temporary in temporary_files:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    for path, device, inode in installed_files:
        try:
            info = path.lstat()
            if info.st_dev == device and info.st_ino == inode:
                path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def bootstrap(
    *,
    admin_url_file,
    database,
    workspace,
    timezone_name,
    output_directory,
    runner_environment=None,
):
    """Create a single disposable Learning database and its private client files."""
    roles = _validate_inputs(database, workspace, timezone_name)
    admin_url = _read_admin_url(admin_url_file)
    try:
        psycopg, conninfo_to_dict, make_conninfo = _load_driver()
    except Exception:
        raise BootstrapError("postgres_driver_unavailable") from None
    host, port = _verify_runner_ownership(
        admin_url, conninfo_to_dict, runner_environment
    )

    with _claimed_output_directory(output_directory) as output_claim:
        _assert_targets_absent(psycopg, admin_url, database, roles)

        writer_password = _generate_secret()
        reader_password = _generate_secret()
        study_token = _generate_secret()
        operator_token = _generate_secret()
        all_secrets = (writer_password, reader_password, study_token, operator_token)
        if any(len(secret) < 32 for secret in all_secrets) or len(set(all_secrets)) != 4:
            raise BootstrapError("secret_generation_invalid")

        targets = {
            "database": database,
            "owner_role": roles["owner"],
            "writer_role": roles["writer"],
            "reader_role": roles["reader"],
        }
        try:
            provision, migrate, grant_runtime = _load_owner_apis()
        except Exception:
            raise BootstrapError("owner_apis_unavailable") from None

        try:
            provision(
                admin_url,
                database,
                roles["owner"],
                roles["writer"],
                roles["reader"],
                writer_password,
                reader_password,
            )
        except Exception:
            raise BootstrapError("owner_operation_failed", "provision", targets) from None

        try:
            owner_url = make_conninfo(
                admin_url,
                dbname=database,
                options="-c role=" + roles["owner"],
            )
            migrate(owner_url, workspace, timezone_name)
        except Exception:
            raise BootstrapError("owner_operation_failed", "migrate", targets) from None

        try:
            grant_runtime(owner_url, roles["writer"], roles["reader"])
        except Exception:
            raise BootstrapError("owner_operation_failed", "grant_runtime", targets) from None

        writer_dsn = make_conninfo(
            host=host,
            port=port,
            dbname=database,
            user=roles["writer"],
        )
        reader_dsn = make_conninfo(
            host=host,
            port=port,
            dbname=database,
            user=roles["reader"],
        )
        manifest = {
            "schema_version": 1,
            "database": database,
            "workspace_id": workspace,
            "timezone": timezone_name,
            "owner_role": roles["owner"],
            "writer_role": roles["writer"],
            "reader_role": roles["reader"],
            "writer_dsn": writer_dsn,
            "reader_dsn": reader_dsn,
            "pgpass_file": "runtime.pgpass",
            "study_token_file": "study.token",
            "operator_token_file": "operator.token",
        }
        passfile = "\n".join(
            ":".join(
                _pgpass_escape(value)
                for value in (host, port, database, role, password)
            )
            for role, password in (
                (roles["writer"], writer_password),
                (roles["reader"], reader_password),
            )
        ) + "\n"
        outputs = [
            ("runtime.pgpass", passfile.encode("utf-8")),
            ("study.token", (study_token + "\n").encode("utf-8")),
            ("operator.token", (operator_token + "\n").encode("utf-8")),
            (
                "connection.json",
                (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            ),
        ]
        try:
            _write_private_outputs(output_claim["path"], outputs)
        except BootstrapError as error:
            error.targets = targets
            raise
        output_claim["keep"] = True


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--output-directory", required=True)
    return parser


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        bootstrap(
            admin_url_file=arguments.admin_url_file,
            database=arguments.database,
            workspace=arguments.workspace,
            timezone_name=arguments.timezone,
            output_directory=arguments.output_directory,
        )
    except BootstrapError as error:
        if error.targets:
            targets = " ".join(f"{key}={value}" for key, value in error.targets.items())
            if error.stage in _OWNER_STAGES:
                message = "Bootstrap stopped during provisioning; target identities: "
            else:
                message = "Bootstrap stopped after owner operations; output incomplete; target identities: "
            print(message + targets, file=sys.stderr)
        else:
            print("Bootstrap refused or failed: " + error.code, file=sys.stderr)
        return 1
    except Exception:
        print("Bootstrap failed before a complete output was written.", file=sys.stderr)
        return 1
    print("Bootstrap completed for database=" + arguments.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
