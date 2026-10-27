"""Required affected Games→Study boundary selection, using the existing pytest runner.

Run from any directory: python backend/app/study/qualify_priority.py.
Combine #487 deferred intake and #488 functional integration source first. Missing
required files/cases fail pytest collection; there are no optional fallbacks.
"""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = (
    "backend/tests/test_study_priority_interface.py",
    "backend/tests/test_product_contracts.py::test_new_product_boundaries_do_not_import_composition_or_private_siblings",
    "backend/tests/test_study_encounter_intake_boundary.py::test_intake_never_calls_queue_and_commits_once_when_refresh_fails",
    "backend/tests/test_study_encounter_intake_boundary.py::test_study_refresh_without_games_consumes_accepted_priority",
    "backend/tests/test_product_deferred_priority_integration.py::test_real_shape_history_intake_commits_pending_then_study_refresh_without_games",
    "backend/tests/test_product_encounter_integration.py::test_unintroduced_encounter_intake_commits_pending_without_queue_capability",
    "backend/tests/test_product_encounter_integration.py::test_pending_encounter_resumes_on_existing_durable_pause_and_task_fence_without_games",
    "backend/tests/test_product_feedback_integration.py::test_maintenance_cold_import_has_no_games_dependency",
)


def main(arguments=()):
    paths = [ROOT / "backend", ROOT / "database"]
    environment = os.environ | {"PYTHONPATH": os.pathsep.join(map(str, paths)), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *REQUIRED, *arguments],
        cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
