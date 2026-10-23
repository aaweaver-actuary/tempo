"""Pure fixture-profile contract, loaded without the database parent package."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tempo_learning_database"
    / "tooling"
    / "fixture_plan.py"
)


def load_fixture_plan():
    specification = importlib.util.spec_from_file_location(
        "learning_fixture_plan_under_test", MODULE_PATH
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Could not load fixture_plan.py by file path")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class LearningFixturePlanTests(unittest.TestCase):
    def test_essential_components_are_exact(self):
        fixture_plan = load_fixture_plan()
        self.assertEqual(fixture_plan.fixture_components("essential"), ("learning",))

    def test_full_components_have_exact_order(self):
        fixture_plan = load_fixture_plan()
        self.assertEqual(
            fixture_plan.fixture_components("full"),
            ("learning", "game-evidence", "content", "analysis-a-bridge"),
        )

    def test_repeated_and_interleaved_calls_are_deterministic(self):
        fixture_plan = load_fixture_plan()
        for profile in ("essential", "full", "full", "essential", "full", "essential"):
            with self.subTest(profile=profile):
                expected = (
                    ("learning",)
                    if profile == "essential"
                    else ("learning", "game-evidence", "content", "analysis-a-bridge")
                )
                self.assertEqual(fixture_plan.fixture_components(profile), expected)

    def test_empty_unknown_and_non_exact_profile_names_are_rejected(self):
        fixture_plan = load_fixture_plan()
        for profile in ("", "unknown", "Essential", "FULL", " essential", "full ", "full\n"):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    fixture_plan.fixture_components(profile)

    def test_fresh_import_and_valid_calls_need_no_subprocess_or_network(self):
        with (
            patch(
                "subprocess.Popen", side_effect=AssertionError("subprocess forbidden")
            ) as popen,
            patch(
                "socket.socket", side_effect=AssertionError("socket forbidden")
            ) as socket,
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network connection forbidden"),
            ) as create_connection,
        ):
            fixture_plan = load_fixture_plan()
            self.assertEqual(fixture_plan.fixture_components("essential"), ("learning",))
            self.assertEqual(
                fixture_plan.fixture_components("full"),
                ("learning", "game-evidence", "content", "analysis-a-bridge"),
            )
            popen.assert_not_called()
            socket.assert_not_called()
            create_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
