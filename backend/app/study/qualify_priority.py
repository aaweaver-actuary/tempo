"""Required affected Games→Study boundary selection, using the existing pytest runner.

Run from any directory: python backend/app/study/qualify_priority.py.
Combine #487 deferred intake and #488 functional integration source first. Missing
required files/cases fail pytest collection; there are no optional fallbacks.
"""
from pathlib import Path
import fcntl
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
    # Reuse the receiving owner's existing source-overlay qualification inputs;
    # select the caller's already-installed Python, never install dependencies.
    sources = (
        "backend", "database", "packages/repertoire-comparison/src",
        "packages/game-classification/src", "packages/game-ingestion/src",
        "packages/game-review-contracts/src", "packages/evidence-math/src",
        "packages/scheduling/src", "packages/chess-core/src",
        "services/learning/runtime/src", "services/learning/application/src",
        "services/learning/remediation/src", "contracts/game-evidence/src",
        "contracts/analysis-jobs/src",
    )
    paths = [ROOT / source for source in sources]
    environment = os.environ | {"PYTHONPATH": os.pathsep.join(map(str, paths)), "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-m", "pytest", "-q", *REQUIRED, *arguments]
    lock_path = Path(environment.get("TEMPO_VALIDATION_LOCK_PATH", "/tmp/tempo-mac-validation-20261025.lock"))
    if "--collect-only" not in arguments and lock_path.exists():
        # Never truncate or unlink the shared advisory lock. Fail promptly if a
        # native owner is already qualifying; release immediately on completion.
        with lock_path.open("r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("VALIDATION_LOCK_BUSY", file=sys.stderr)
                return 2
            try:
                return subprocess.run(command, cwd=ROOT, env=environment).returncode
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return subprocess.run(command, cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
