import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_STUDY_PATH = (
    REPO_ROOT
    / "services/learning/runtime/integration/run_study.py"
)
ESSENTIAL_SCENARIOS = (
    "normal",
    "teaching",
    "lost-response",
    "before-commit",
    "next-load",
    "two-tabs",
    "offline-shell",
    "wrong-credential",
    "origin-transition",
    "quota",
)


def load_run_study():
    runner_name = "tempo_learning_database.tooling.run_postgres"
    runner = types.ModuleType(runner_name)
    runner.ROOT = REPO_ROOT
    runner.main = mock.Mock()
    database = types.ModuleType("tempo_learning_database")
    database.__path__ = []
    tooling = types.ModuleType("tempo_learning_database.tooling")
    tooling.__path__ = []
    modules = {
        "tempo_learning_database": database,
        "tempo_learning_database.tooling": tooling,
        runner_name: runner,
    }
    module_name = "test_run_study_under_test"
    spec = importlib.util.spec_from_file_location(module_name, RUN_STUDY_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module, runner.main


class StudyEssentialSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_study, cls.owner_main = load_run_study()

    def test_essential_has_ten_unique_exact_node_ids(self):
        study_tests = (
            REPO_ROOT / "services/learning/runtime/integration/study_tests"
        )
        test_file = study_tests / "test_study_ui.py"
        expected = [
            Path(
                f"{test_file}::test_production_study_learning_native[{scenario}]"
            )
            for scenario in ESSENTIAL_SCENARIOS
        ]

        actual = self.run_study.study_test_paths("essential")

        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 10)
        self.assertEqual(len(set(actual)), 10)

    def invoke_run_with_doubles(self, profile, evidence):
        build = evidence.parent / "mock-build"
        build.mkdir()
        self.owner_main.reset_mock()
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(
                self.run_study.tempfile, "mkdtemp", return_value=str(build)
            ),
            mock.patch.object(self.run_study.shutil, "rmtree"),
            mock.patch.object(
                self.run_study.subprocess, "run", return_value=completed
            ) as subprocess_run,
            mock.patch.object(
                self.run_study.sys,
                "argv",
                [str(RUN_STUDY_PATH), str(evidence)],
            ),
        ):
            self.run_study.run(profile=profile)
        return subprocess_run

    def test_full_keeps_directory_selection_and_default_fixture_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "full-evidence"
            subprocess_run = self.invoke_run_with_doubles("full", evidence)
            study_tests = (
                REPO_ROOT / "services/learning/runtime/integration/study_tests"
            )
            self.assertEqual(
                self.run_study.study_test_paths("full"), [study_tests]
            )
            self.assertEqual(subprocess_run.call_count, 2)
            self.owner_main.assert_called_once()
            args, kwargs = self.owner_main.call_args
            self.assertEqual(args[0], [study_tests])
            self.assertNotIn("fixture_profile", kwargs)
            self.assertEqual(kwargs, {"evidence_directory": evidence})

    def test_unknown_profile_fails_before_resources_or_subprocess(self):
        self.owner_main.reset_mock()
        with (
            mock.patch.object(self.run_study.subprocess, "run") as subprocess_run,
            mock.patch.object(self.run_study.tempfile, "mkdtemp") as make_build,
            mock.patch.object(self.run_study.Path, "mkdir") as make_directory,
        ):
            with self.assertRaisesRegex(ValueError, "Unknown study profile"):
                self.run_study.run(profile="not-a-profile")

        subprocess_run.assert_not_called()
        make_build.assert_not_called()
        make_directory.assert_not_called()
        self.owner_main.assert_not_called()

    def test_essential_forwards_only_selected_paths_and_fixture_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "essential-evidence"
            subprocess_run = self.invoke_run_with_doubles("essential", evidence)
            selected = self.run_study.study_test_paths("essential")

            self.assertEqual(subprocess_run.call_count, 2)
            self.owner_main.assert_called_once()
            args, kwargs = self.owner_main.call_args
            self.assertEqual(args[0], selected)
            self.assertEqual(len(args[0]), 10)
            self.assertEqual(
                kwargs,
                {
                    "evidence_directory": evidence,
                    "fixture_profile": "essential",
                },
            )


if __name__ == "__main__":
    unittest.main()
