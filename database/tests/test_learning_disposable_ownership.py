"""Pure local tests for disposable Learning ownership receipts."""

import json
import io
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch


TOOLING_DIRECTORY = Path(__file__).resolve().parents[1] / "tempo_learning_database" / "tooling"
sys.path.insert(0, str(TOOLING_DIRECTORY))

import disposable_ownership as ownership


INVOCATION_ID = "0123456789abcdef"
PROJECT = "tempo-study-test-" + INVOCATION_ID
DATABASE = "learning_study_test_" + INVOCATION_ID
CONTAINER_ID = "a" * 64
NETWORK_ID = "b" * 64
SYSTEM_IDENTIFIER = "7483920012345678901"


def receipt():
    return {
        "schema_version": 1,
        "invocation_id": INVOCATION_ID,
        "project_name": PROJECT,
        "database": DATABASE,
        "container_id": CONTAINER_ID,
        "volume_name": PROJECT + "_postgres-data",
        "network_id": NETWORK_ID,
        "network_name": PROJECT + "_default",
        "service": "postgres",
        "host": "postgres",
        "port": 5432,
        "system_identifier": SYSTEM_IDENTIFIER,
    }


def inspect_responses(*, container=None, volume=None, network=None):
    labels = {
        "tempo.owner": PROJECT,
        "tempo.disposable": "true",
        "com.docker.compose.project": PROJECT,
    }
    container_value = container or {
        "Id": CONTAINER_ID,
        "Config": {
            "Labels": {
                **labels,
                "com.docker.compose.service": "postgres",
            }
        },
        "State": {"Running": True},
        "Mounts": [
            {
                "Type": "volume",
                "Name": PROJECT + "_postgres-data",
                "Destination": "/var/lib/postgresql",
            }
        ],
        "NetworkSettings": {
            "Ports": {"5432/tcp": None},
            "Networks": {
                PROJECT + "_default": {
                    "NetworkID": NETWORK_ID,
                    "Aliases": [PROJECT + "-postgres-1", "postgres"],
                }
            },
        },
    }
    volume_value = volume or {
        "Name": PROJECT + "_postgres-data",
        "Labels": {
            **labels,
            "com.docker.compose.volume": "postgres-data",
        },
    }
    network_value = network or {
        "Name": PROJECT + "_default",
        "Id": NETWORK_ID,
        "Labels": {
            **labels,
            "com.docker.compose.network": "default",
        },
        "Containers": {CONTAINER_ID: {"Name": PROJECT + "-postgres-1"}},
    }
    return [
        SimpleNamespace(returncode=0, stdout=CONTAINER_ID + "\n"),
        SimpleNamespace(returncode=0, stdout=json.dumps([container_value])),
        SimpleNamespace(returncode=0, stdout=json.dumps([volume_value])),
        SimpleNamespace(returncode=0, stdout=json.dumps([network_value])),
        SimpleNamespace(
            returncode=0,
            stdout="Database cluster state:               in production\n"
            "Database system identifier:             "
            + SYSTEM_IDENTIFIER
            + "\n",
        ),
    ]


class DisposableOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.invocation_directory = self.root / INVOCATION_ID
        self.invocation_directory.mkdir(mode=0o700)
        self.invocation_directory.chmod(0o700)
        self.receipt_path = self.invocation_directory / "ownership.json"
        self.compose_file = self.root / "compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.env_file = self.root / "study.env"
        self.env_file.write_text(
            "COMPOSE_PROJECT_NAME=ambient-project\n"
            "TEMPO_STUDY_PRIVATE_SENTINEL=do-not-print-this\n",
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)

    def tearDown(self):
        self.temporary.cleanup()

    def write_receipt(self, data=None, *, mode=0o600):
        self.receipt_path.write_text(
            json.dumps(receipt() if data is None else data), encoding="utf-8"
        )
        self.receipt_path.chmod(mode)
        return self.receipt_path

    def produce(self, *, responses=None, **overrides):
        if responses is None:
            responses = inspect_responses(**overrides)
        with patch.object(ownership.subprocess, "run", side_effect=responses) as run:
            result = ownership.produce_ownership_receipt(
                compose_file=self.compose_file,
                env_file=self.env_file,
                invocation_directory=self.invocation_directory,
                invocation_id=INVOCATION_ID,
                database=DATABASE,
            )
        return result, run

    def test_loads_valid_receipt_with_private_modes(self):
        self.write_receipt()
        loaded = ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
        self.assertEqual(loaded, receipt())

    def test_cli_requires_and_forwards_explicit_private_env_file(self):
        base_arguments = [
            "--compose-file",
            str(self.compose_file),
            "--invocation-directory",
            str(self.invocation_directory),
            "--invocation-id",
            INVOCATION_ID,
            "--database",
            DATABASE,
        ]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ownership._parser().parse_args(base_arguments)

        output = io.StringIO()
        with patch.object(
            ownership, "produce_ownership_receipt", return_value=receipt()
        ) as produce, redirect_stdout(output):
            result = ownership.main(
                base_arguments + ["--env-file", str(self.env_file)]
            )
        self.assertEqual(result, 0)
        self.assertEqual(produce.call_args.kwargs["env_file"], str(self.env_file))
        self.assertEqual(output.getvalue(), str(self.receipt_path) + "\n")
        self.assertNotIn("do-not-print-this", output.getvalue())

    def test_producer_rejects_non_private_or_symlink_env_file(self):
        self.env_file.chmod(0o644)
        with patch.object(ownership.subprocess, "run") as run:
            with self.assertRaises(ownership.OwnershipError) as raised:
                self.produce()
        self.assertEqual(raised.exception.code, "env_file_permissions")
        run.assert_not_called()

        self.env_file.chmod(0o600)
        symlink = self.root / "linked.env"
        symlink.symlink_to(self.env_file)
        with patch.object(ownership.subprocess, "run") as run:
            with self.assertRaises(ownership.OwnershipError) as raised:
                ownership.produce_ownership_receipt(
                    compose_file=self.compose_file,
                    env_file=symlink,
                    invocation_directory=self.invocation_directory,
                    invocation_id=INVOCATION_ID,
                    database=DATABASE,
                )
        self.assertEqual(raised.exception.code, "env_file_invalid")
        run.assert_not_called()

    def test_receipt_schema_requires_exact_keys_and_unique_json_keys(self):
        missing = receipt()
        del missing["network_id"]
        extra = {**receipt(), "raw_inspect": "must not be accepted"}
        for value in (missing, extra):
            with self.subTest(value=value):
                self.write_receipt(value)
                with self.assertRaises(ownership.OwnershipError) as raised:
                    ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
                self.assertEqual(raised.exception.code, "receipt_schema_invalid")

        self.receipt_path.write_text(
            json.dumps(receipt())[:-1] + ',"host":"postgres"}', encoding="utf-8"
        )
        self.receipt_path.chmod(0o600)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
        self.assertEqual(raised.exception.code, "receipt_unavailable")

    def test_rejects_boolean_integer_fields_and_invalid_types(self):
        cases = []
        for field, value in (
            ("schema_version", True),
            ("port", True),
            ("port", "5432"),
            ("invocation_id", 123),
            ("container_id", "A" * 64),
            ("network_id", "b" * 63),
            ("system_identifier", "12x"),
            ("system_identifier", 1234),
        ):
            invalid = receipt()
            invalid[field] = value
            cases.append((field, invalid))
        for field, value in cases:
            with self.subTest(field=field, value=value[field]):
                self.write_receipt(value)
                with self.assertRaises(ownership.OwnershipError) as raised:
                    ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
                self.assertIn(
                    raised.exception.code,
                    {"receipt_schema_invalid", "receipt_identity_mismatch"},
                )

    def test_rejects_database_and_shared_invocation_mismatches(self):
        self.write_receipt()
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(
                self.receipt_path, database="learning_study_test_ffffffffffffffff"
            )
        self.assertEqual(raised.exception.code, "receipt_identity_mismatch")

        invalid = receipt()
        invalid["database"] = "learning_study_test_ffffffffffffffff"
        self.write_receipt(invalid)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
        self.assertEqual(raised.exception.code, "receipt_identity_mismatch")

    def test_rejects_unsafe_paths_and_symlinks(self):
        self.write_receipt()
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(
                str(self.receipt_path.parent) + "/../" + INVOCATION_ID + "/ownership.json",
                database=DATABASE,
            )
        self.assertEqual(raised.exception.code, "receipt_path_invalid")

        symlink = self.invocation_directory / "linked.json"
        symlink.symlink_to(self.receipt_path)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(symlink, database=DATABASE)
        self.assertEqual(raised.exception.code, "receipt_path_invalid")

        other_directory = self.root / "other"
        other_directory.mkdir()
        self.receipt_path.unlink()
        (self.invocation_directory / "linked.json").unlink()
        linked_directory = self.root / INVOCATION_ID
        linked_directory.rmdir()
        linked_directory.symlink_to(other_directory, target_is_directory=True)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(
                linked_directory / "ownership.json", database=DATABASE
            )
        self.assertEqual(raised.exception.code, "receipt_path_invalid")

    def test_requires_exact_receipt_and_invocation_directory_modes(self):
        self.write_receipt(mode=0o640)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
        self.assertEqual(raised.exception.code, "receipt_file_permissions")

        self.receipt_path.chmod(0o600)
        self.invocation_directory.chmod(0o750)
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.load_ownership_receipt(self.receipt_path, database=DATABASE)
        self.assertEqual(raised.exception.code, "receipt_directory_permissions")

    def test_producer_attests_owned_compose_postgres_and_writes_private_receipt(self):
        result, run = self.produce()
        self.assertEqual(result, receipt())
        self.assertTrue(self.receipt_path.is_file())
        self.assertEqual(stat.S_IMODE(self.receipt_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.invocation_directory.stat().st_mode), 0o700)
        self.assertEqual(
            ownership.load_ownership_receipt(self.receipt_path, database=DATABASE),
            receipt(),
        )
        self.assertEqual(run.call_count, 5)
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in run.call_args_list))
        self.assertTrue(all(call.kwargs["capture_output"] for call in run.call_args_list))
        compose_command = run.call_args_list[0].args[0]
        self.assertEqual(
            compose_command,
            [
                "docker",
                "compose",
                "--env-file",
                str(self.env_file),
                "--project-name",
                PROJECT,
                "--file",
                str(self.compose_file),
                "ps",
                "--status",
                "running",
                "--quiet",
                "postgres",
            ],
        )
        self.assertNotIn("COMPOSE_PROJECT_NAME=ambient-project", compose_command)
        self.assertNotIn("do-not-print-this", str(run.call_args_list))
        self.assertNotIn("env", run.call_args.kwargs)
        self.assertEqual(
            run.call_args_list[-1].args[0][:4],
            ["docker", "exec", CONTAINER_ID, "sh"],
        )

    def test_rejects_container_volume_network_and_mount_ownership_mismatches(self):
        variations = []
        container = inspect_responses()[1]
        container_data = json.loads(container.stdout)[0]
        bad = json.loads(json.dumps(container_data))
        bad["Config"]["Labels"]["tempo.owner"] = "other-project"
        variations.append(("container label", inspect_responses(container=bad), "ownership_labels_mismatch"))

        bad = json.loads(json.dumps(container_data))
        bad["Config"]["Labels"]["com.docker.compose.service"] = "web"
        variations.append(("compose service", inspect_responses(container=bad), "ownership_labels_mismatch"))

        bad = json.loads(json.dumps(container_data))
        bad["Mounts"][0]["Name"] = "other-volume"
        variations.append(("mount", inspect_responses(container=bad), "ownership_mount_mismatch"))

        volume = inspect_responses()[2]
        volume_data = json.loads(volume.stdout)[0]
        bad = json.loads(json.dumps(volume_data))
        bad["Labels"]["tempo.disposable"] = "false"
        variations.append(("volume label", inspect_responses(volume=bad), "ownership_labels_mismatch"))

        network = inspect_responses()[3]
        network_data = json.loads(network.stdout)[0]
        bad = json.loads(json.dumps(network_data))
        bad["Labels"]["tempo.owner"] = "other-project"
        variations.append(("network label", inspect_responses(network=bad), "ownership_labels_mismatch"))

        for name, responses, expected_code in variations:
            with self.subTest(name=name), patch.object(
                ownership.subprocess, "run", side_effect=responses
            ):
                with self.assertRaises(ownership.OwnershipError) as raised:
                    ownership.produce_ownership_receipt(
                        compose_file=self.compose_file,
                        env_file=self.env_file,
                        invocation_directory=self.invocation_directory,
                        invocation_id=INVOCATION_ID,
                        database=DATABASE,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse(self.receipt_path.exists())

    def test_rejects_unexpected_network_alias_and_published_host_port(self):
        responses = inspect_responses()
        container = json.loads(responses[1].stdout)[0]
        container["NetworkSettings"]["Networks"][PROJECT + "_default"]["Aliases"] = ["wrong"]
        responses = inspect_responses(container=container)
        with patch.object(ownership.subprocess, "run", side_effect=responses):
            with self.assertRaises(ownership.OwnershipError) as raised:
                ownership.produce_ownership_receipt(
                    compose_file=self.compose_file,
                    env_file=self.env_file,
                    invocation_directory=self.invocation_directory,
                    invocation_id=INVOCATION_ID,
                    database=DATABASE,
                )
        self.assertEqual(raised.exception.code, "ownership_network_mismatch")

        responses = inspect_responses()
        container = json.loads(responses[1].stdout)[0]
        container["NetworkSettings"]["Ports"]["5432/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": "55432"}
        ]
        responses = inspect_responses(container=container)
        with patch.object(ownership.subprocess, "run", side_effect=responses):
            with self.assertRaises(ownership.OwnershipError) as raised:
                ownership.produce_ownership_receipt(
                    compose_file=self.compose_file,
                    env_file=self.env_file,
                    invocation_directory=self.invocation_directory,
                    invocation_id=INVOCATION_ID,
                    database=DATABASE,
                )
        self.assertEqual(raised.exception.code, "ownership_published_port")

    def test_command_failure_and_timeout_are_bounded_safe_errors(self):
        for failure, code in (
            (SimpleNamespace(returncode=1, stdout="secret output"), "ownership_command_failed"),
            (TimeoutError("simulated timeout"), "ownership_command_failed"),
        ):
            with self.subTest(code=code), patch.object(
                ownership.subprocess, "run", side_effect=failure
            ) as run:
                with self.assertRaises(ownership.OwnershipError) as raised:
                    ownership.produce_ownership_receipt(
                        compose_file=self.compose_file,
                        env_file=self.env_file,
                        invocation_directory=self.invocation_directory,
                        invocation_id=INVOCATION_ID,
                        database=DATABASE,
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("secret output", str(raised.exception))
                self.assertLessEqual(run.call_args.kwargs["timeout"], 30)

        with patch.object(
            ownership.subprocess,
            "run",
            side_effect=ownership.subprocess.TimeoutExpired("docker", 10),
        ):
            with self.assertRaises(ownership.OwnershipError) as raised:
                ownership.produce_ownership_receipt(
                    compose_file=self.compose_file,
                    env_file=self.env_file,
                    invocation_directory=self.invocation_directory,
                    invocation_id=INVOCATION_ID,
                    database=DATABASE,
                )
        self.assertEqual(raised.exception.code, "ownership_command_timeout")

    def test_producer_rejects_mismatched_database_and_never_overwrites(self):
        with self.assertRaises(ownership.OwnershipError) as raised:
            ownership.produce_ownership_receipt(
                compose_file=self.compose_file,
                env_file=self.env_file,
                invocation_directory=self.invocation_directory,
                invocation_id=INVOCATION_ID,
                database="learning_study_test_ffffffffffffffff",
            )
        self.assertEqual(raised.exception.code, "database_invocation_mismatch")

        result, _run = self.produce()
        original = self.receipt_path.read_bytes()
        with patch.object(ownership.subprocess, "run") as run:
            with self.assertRaises(ownership.OwnershipError) as raised:
                ownership.produce_ownership_receipt(
                    compose_file=self.compose_file,
                    env_file=self.env_file,
                    invocation_directory=self.invocation_directory,
                    invocation_id=INVOCATION_ID,
                    database=DATABASE,
                )
        self.assertEqual(raised.exception.code, "receipt_would_overwrite")
        run.assert_not_called()
        self.assertEqual(self.receipt_path.read_bytes(), original)
        self.assertEqual(result, receipt())


if __name__ == "__main__":
    unittest.main()
