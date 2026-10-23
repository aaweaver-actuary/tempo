"""Exercise native-runner profiles without subprocesses, sockets or Docker."""

from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "tempo_learning_database/tooling/run_postgres.py"
)
SPECIFICATION = importlib.util.spec_from_file_location("learning_profile_runner", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(RUNNER)

CORE_ROOTS = {
    "learning-remediation": "services/learning/remediation",
    "learning-review-application": "services/learning/application",
    "scheduling": "packages/scheduling",
    "chess-core": "packages/chess-core",
    "content-publication-contracts": "contracts/content-publication",
    "game-evidence-contracts": "contracts/game-evidence",
}
OPTIONAL_ROOTS = {
    "game-evidence": "services/game-evidence",
    "content": "services/content",
    "analysis-a-bridge": "tools/analysis-restoration/a_bridge",
}
FULL_ROOTS = {
    **CORE_ROOTS, **OPTIONAL_ROOTS,
    "browser-intents": "contracts/browser-intents",
    "study-session": "packages/study-session",
}
OPTIONAL_VARIABLES = (
    "TEMPO_LEARNING_TEST_GAME_EVIDENCE_PYTHON",
    "TEMPO_LEARNING_TEST_CONTENT_CLI",
    "TEMPO_LEARNING_TEST_BRIDGE_CLI",
    "TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE",
)
NODE_ID = "services/learning/runtime/tests/test_runtime.py::test_core"


class LearningFixtureProfilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.evidence = self.root / "evidence"
        for relative in [
            *(root + "/private/input.py" for root in FULL_ROOTS.values()),
            "app/lib/input.ts", "app/domain/input.ts", "app/const.ts",
            "database/tempo_learning_database/input.py",
            *("services/learning/runtime/tests/" + name for name in (
                "test_runtime.py", "test_publications.py", "test_restart.py", "test_container.py"
            )),
        ]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture input\n")
        (self.root / "components.json").write_text(json.dumps({"components": [
            {"id": owner, "root": root, "buildInputs": ["private"]}
            for owner, root in FULL_ROOTS.items()
        ]}))
        self.commands = []
        self.docker_commands = []
        self.source_reads = []
        self.traversals = []
        self.protect_optional = False
        self.container_present = False
        self.volume_present = False
        self.wrong_owner = False
        self.inventory_fault = None
        self.execution_failure = False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("ROOT", self.root),
            ("APPLICATION", self.root / "services/learning/application"),
        ):
            self.stack.enter_context(patch.object(RUNNER, name, value))
        self.stack.enter_context(patch.object(sys, "argv", ["run_postgres.py"]))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(RUNNER.secrets, "token_hex", return_value="unit"))
        self.stack.enter_context(patch.object(RUNNER.subprocess, "check_output", return_value="tested-sha\n"))
        self.stack.enter_context(patch.object(RUNNER.subprocess, "Popen", side_effect=AssertionError("real subprocess forbidden")))
        self.stack.enter_context(patch.object(RUNNER.subprocess, "run", side_effect=self.run_command))
        self.stack.enter_context(patch.object(RUNNER, "docker", side_effect=self.docker))
        listener = MagicMock()
        listener.getsockname.return_value = ("127.0.0.1", 65432)
        self.stack.enter_context(patch.object(RUNNER.socket, "socket", return_value=listener))
        self.stack.enter_context(patch.object(RUNNER.socket, "create_connection", side_effect=AssertionError("network forbidden")))
        database = MagicMock()
        database.execute.return_value.fetchall.return_value = [("max_locks_per_transaction", "512")]
        connection = MagicMock()
        connection.__enter__.return_value = database
        psycopg = ModuleType("psycopg")
        psycopg.connect = MagicMock(return_value=connection)
        psycopg.OperationalError = type("OperationalError", (Exception,), {})
        conninfo = ModuleType("psycopg.conninfo")
        conninfo.make_conninfo = lambda **kwargs: "disposable-test-admin"
        self.stack.enter_context(patch.dict(sys.modules, {"psycopg": psycopg, "psycopg.conninfo": conninfo}))
        original_read = Path.read_bytes
        original_rglob = Path.rglob

        def record(path, destinations):
            destinations.append(path)
            if self.protect_optional and any(
                path.is_relative_to(self.root / root) for root in OPTIONAL_ROOTS.values()
            ):
                raise AssertionError("Optional private source touched: " + str(path))

        def read_bytes(path):
            record(path, self.source_reads)
            return original_read(path)

        def rglob(path, pattern):
            record(path, self.traversals)
            return original_rglob(path, pattern)

        self.stack.enter_context(patch.object(Path, "read_bytes", read_bytes))
        self.stack.enter_context(patch.object(Path, "rglob", rglob))

    def docker(self, *arguments):
        self.docker_commands.append(arguments)
        if arguments[:2] == ("volume", "create"):
            self.volume_present = True
        elif arguments[0] == "run":
            self.container_present = True
        elif arguments[:2] == ("container", "inspect"):
            return json.dumps({"cpu_nanos": 1_000_000_000, "memory_bytes": 512 * 1024 * 1024})
        elif arguments[:2] == ("container", "ls"):
            return "owned-container" if self.container_present else ""
        elif arguments[:2] == ("volume", "ls"):
            return "tempo-learning-unit-data" if self.volume_present else ""
        elif "--format" in arguments and "Labels" in arguments[-1]:
            return json.dumps({"tempo.owner": "other" if self.wrong_owner else "tempo-learning-unit", "tempo.disposable": "true"})
        elif arguments[0] == "rm":
            self.container_present = False
        elif arguments[:2] == ("volume", "rm"):
            self.volume_present = False
        elif arguments[0] == "inspect":
            return "65432"
        return "sha256:fixture"

    def run_command(self, command, **kwargs):
        self.commands.append((command, dict(kwargs.get("env", {}))))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"tempo.owner": "tempo-learning-unit"}), stderr="")
        if len(command) > 1 and command[1].endswith("qualification.py"):
            collecting = "--collect-only" in command
            identities = [] if self.inventory_fault == "empty" else [NODE_ID]
            if not collecting and self.inventory_fault == "mismatch":
                identities = [NODE_ID + "_different"]
            reports = [] if collecting else [
                {"nodeid": identity, "phase": phase,
                 "outcome": "skipped" if self.inventory_fault == "skip" else "passed"}
                for identity in identities for phase in ("setup", "call", "teardown")
            ]
            if not collecting and self.inventory_fault == "phase":
                reports.pop()
            Path(command[2]).write_text(json.dumps({
                "collected": identities, "deselected": [], "collection_errors": [],
                "reports": reports, "exit_code": 0,
            }))
            if not collecting:
                junit = next(part.split("=", 1)[1] for part in command if part.startswith("--junitxml="))
                cases = "" if self.inventory_fault == "junit" else '<testcase classname="tests.test_runtime" name="test_core"/>'
                Path(junit).write_text("<testsuite>" + cases + "</testsuite>")
                temporary = Path(next(part.split("=", 1)[1] for part in command if part.startswith("--basetemp=")))
                temporary.mkdir()
                (temporary / "server-core.log").write_text("service log")
            return SimpleNamespace(returncode=int(self.execution_failure and not collecting), stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def invoke(self, **kwargs):
        kwargs.setdefault("test_paths", [Path(NODE_ID)])
        kwargs.setdefault("evidence_directory", self.evidence)
        with redirect_stderr(io.StringIO()):
            RUNNER.main(**kwargs)
        return json.loads((self.evidence / "native.json").read_text())

    def test_essential_omits_optional_installs_sources_and_environment(self):
        self.protect_optional = True
        os.environ.update({name: "ambient-optional" for name in OPTIONAL_VARIABLES})
        report = self.invoke(fixture_profile="essential")
        installs = [command for command, _ in self.commands if command[0] == "uv"]
        self.assertEqual([command[-1] for command in installs], ["tempo-learning-runtime"])
        for _, environment in self.commands:
            self.assertFalse(set(OPTIONAL_VARIABLES) & environment.keys())
        expected = {root + "/private/input.py" for root in CORE_ROOTS.values()} | {"components.json"}
        self.assertEqual(set(report["dependency_inputs"]), expected)
        self.assertEqual(report["fixture_components"], ["learning"])

    def test_default_full_preserves_prior_installs_sources_and_cli_variables(self):
        report = self.invoke()
        installs = [command for command, _ in self.commands if command[0] == "uv"]
        self.assertEqual([command[-1] for command in installs], [
            "tempo-game-evidence", "tempo-learning-runtime", "tempo-content", "tempo-a-bridge"
        ])
        expected = {root + "/private/input.py" for root in FULL_ROOTS.values()} | {
            "components.json", "app/lib/input.ts", "app/domain/input.ts", "app/const.ts"
        }
        self.assertEqual(set(report["dependency_inputs"]), expected)
        self.assertTrue(set(OPTIONAL_VARIABLES) <= self.commands[-1][1].keys())
        self.assertEqual(report["fixture_profile"], "full")

    def test_invalid_profile_and_unbounded_selection_reject_before_resources(self):
        for arguments in [
            {"fixture_profile": ""}, {"fixture_profile": "unknown"},
            *({"fixture_profile": "essential", "test_paths": selection} for selection in (
                None, [], [Path("services/learning/runtime/tests")], [Path("missing.py::test")],
            )),
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.invoke(**arguments)
        self.assertFalse(self.evidence.exists())
        self.assertEqual(self.docker_commands, [])
        self.assertEqual(self.commands, [])

    def test_essential_rejects_optional_source_and_cli_overrides_before_resources(self):
        for arguments in [
            {"extra_sources": [self.root / "services/content"]},
            {"extra_sources": [self.root]},
            {"additional_environment": {OPTIONAL_VARIABLES[0]: "producer-python"}},
        ]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.invoke(fixture_profile="essential", **arguments)
        self.assertFalse(self.evidence.exists())
        self.assertEqual(self.docker_commands, [])

    def test_path_node_selection_counts_junit_and_owned_cleanup_are_retained(self):
        external_junit = self.root / "external/native.xml"
        os.environ["PYTEST_ADDOPTS"] = "--junitxml=" + str(external_junit)
        report = self.invoke(fixture_profile="essential")
        self.assertEqual(report["identities"], [NODE_ID])
        self.assertEqual(report["passed"], 1)
        self.assertTrue(report["complete"])
        for command in report["commands"]:
            self.assertIn(NODE_ID, command)
        self.assertIn("--collect-only", report["commands"][0])
        self.assertTrue(external_junit.is_file())
        for name in ("container_absent", "volume_absent", "temporary_test_data_removed"):
            self.assertIs(report[name], True)
        self.assertFalse(self.container_present)
        self.assertFalse(self.volume_present)
        self.assertTrue((Path(report["evidence"]) / "service-logs/server-core.log").is_file())

    def test_container_file_and_node_ids_require_explicit_image_build(self):
        for suffix in ("", "::test_installed_container"):
            with self.subTest(suffix=suffix):
                self.evidence = self.root / ("container-evidence-" + str(len(suffix)))
                report = self.invoke(fixture_profile="essential", test_paths=[
                    Path("services/learning/runtime/tests/test_container.py" + suffix)
                ])
                self.assertIs(report["runtime_image_required"], True)
                self.assertIs(report["runtime_image_tag_removed"], True)
        self.assertEqual(sum(command[:2] == ["docker", "build"] for command, _ in self.commands), 2)

    def test_other_core_files_do_not_build_runtime_image(self):
        for name in ("test_runtime.py", "test_publications.py", "test_restart.py"):
            with self.subTest(name=name):
                self.evidence = self.root / name
                report = self.invoke(fixture_profile="essential", test_paths=[
                    Path("services/learning/runtime/tests") / name
                ])
                self.assertIs(report["runtime_image_required"], False)
        self.assertFalse(any(command[:2] == ["docker", "build"] for command, _ in self.commands))

    def test_full_directory_selection_still_builds_runtime_image(self):
        report = self.invoke(test_paths=[Path("services/learning/runtime/tests")])
        self.assertIs(report["runtime_image_required"], True)

    def test_empty_mismatched_skipped_incomplete_and_junit_inventories_fail(self):
        for fault in ("empty", "mismatch", "skip", "phase", "junit"):
            with self.subTest(fault=fault):
                self.evidence = self.root / ("fault-" + fault)
                self.inventory_fault = fault
                with self.assertRaises(SystemExit):
                    self.invoke(fixture_profile="essential")
                report = json.loads((self.evidence / "native.json").read_text())
                self.assertFalse(report["complete"])
                self.assertTrue(report["container_absent"])
                self.assertTrue(report["volume_absent"])

    def test_failed_optional_test_is_actionable_without_fallback_or_skip(self):
        self.execution_failure = True
        with self.assertRaises(SystemExit):
            self.invoke(fixture_profile="essential")
        report = json.loads((self.evidence / "native.json").read_text())
        self.assertIn("use fixture_profile='full'", report["error"])
        self.assertEqual(len([command for command, _ in self.commands if command[0] == "uv"]), 1)
        self.assertTrue(report["container_absent"])
        self.assertTrue(report["volume_absent"])

    def test_cleanup_refuses_foreign_owner(self):
        self.wrong_owner = True
        with self.assertRaises(SystemExit):
            self.invoke(fixture_profile="essential")
        self.assertFalse(any(command[0] == "rm" or command[:2] == ("volume", "rm") for command in self.docker_commands))
        report = json.loads((self.evidence / "native.json").read_text())
        self.assertIn("ownership mismatch", report["cleanup_error"])


if __name__ == "__main__":
    unittest.main()
