"""Required affected Games→Study boundary selection, using the existing pytest runner.

Run from any directory: python backend/app/study/qualify_priority.py.
The joined #618–#624 source owns the explicit finite selection below. Missing
required files/cases fail pytest collection; there are no optional fallbacks.
Packaged/browser proof remains the separate explicit games-collection-release suite.
"""

from pathlib import Path
import fcntl
import hashlib
import json
import platform
import shlex
import tempfile
import xml.etree.ElementTree as ET
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = (
    "backend/tests/test_study_priority_interface.py",
    "backend/tests/test_next_card_preparation_selection.py",
    "backend/tests/test_study_qualification_leaf.py",
    "backend/tests/test_study_recovery_acceptance.py",
    "backend/tests/test_game_bound_repertoire_preparation.py",
    "backend/tests/test_study_feedback_preparation_trace.py",
    "backend/tests/test_queue_projection_postgres.py",
    "backend/tests/test_product_game_bound_snapshot_postgres.py",
    "backend/tests/test_product_snapshot.py",
    "backend/tests/test_product_game_lapse.py::test_qualified_owner_prepares_every_selected_publication_beyond_eight_and_rejects_invalid_scope",
    "backend/tests/test_product_game_lapse.py::test_qualified_owner_rejects_producer_shaped_prepared_publication",
    "backend/tests/test_product_contracts.py::test_new_product_boundaries_do_not_import_composition_or_private_siblings",
    "backend/tests/test_study_encounter_intake_boundary.py::test_intake_never_calls_queue_and_commits_once_when_refresh_fails",
    "backend/tests/test_study_encounter_intake_boundary.py::test_study_refresh_without_games_consumes_accepted_priority",
    "backend/tests/test_study_encounter_intake_boundary.py::test_applied_game_priority_allows_bounded_daily_queue_preparation_without_losing_attempts",
    "backend/tests/test_product_deferred_priority_integration.py::test_real_shape_history_intake_commits_pending_then_study_refresh_without_games",
    "backend/tests/test_product_encounter_integration.py::test_unintroduced_encounter_intake_commits_pending_without_queue_capability",
    "backend/tests/test_product_encounter_integration.py::test_pending_encounter_resumes_on_existing_durable_pause_and_task_fence_without_games",
    "backend/tests/test_product_feedback_integration.py::test_maintenance_cold_import_has_no_games_dependency",
    "backend/tests/test_priority_ownership_pickup_postgres.py::test_historical_evidence_ownership_pickup_commits_within_existing_budget_and_replays",
    "backend/tests/test_product_first_boundary.py",
    "backend/tests/test_product_delivery_qualification.py::test_feedback_enable_qualifies_original_collection_outbox",
    "backend/tests/test_product_delivery_qualification.py::test_retained_legacy_lapse_has_compatible_or_terminal_disposition",
    "backend/tests/test_product_delivery_qualification.py::test_qualification_projects_complete_publication_after_reader_exit",
    "backend/tests/test_product_game_review_commands_postgres.py::test_native_unreserved_snapshot_read_profile_refuses_writes_and_keeps_background_budget",
    "backend/tests/test_product_delivery_qualification.py::test_native_live_and_history_deliveries_have_bounded_fair_progress",
    "backend/tests/test_product_delivery_qualification.py::test_native_semantic_qualification_spends_fair_turn_without_transport_attempt",
    "backend/tests/test_product_delivery_qualification.py::test_native_controls_win_after_qualification_and_delayed_ack",
    "backend/tests/test_product_feedback_release.py",
    "backend/tests/test_queue_randomization_feedback_parity.py",
    "backend/tests/test_queue_refresh_read_database_split.py::test_feedback_priority_paths_survive_bounded_sql_translation",
    "backend/tests/test_study_attempt_issuance_postgres.py",
    "backend/tests/test_prepared_review_continuation.py::test_prepared_control_precedes_pending_study_work_without_applying_during_preparation",
    "backend/tests/test_product_recombination_postgres.py::test_qualified_feedback_reaches_current_join_or_split_target_and_real_settlement[False-fresh]",
    "backend/tests/test_product_recombination_postgres.py::test_qualified_feedback_reaches_current_join_or_split_target_and_real_settlement[True-fresh]",
    "backend/tests/test_product_recombination_postgres.py::test_qualified_feedback_reaches_current_join_or_split_target_and_real_settlement[False-fresh_original_available]",
    "backend/tests/test_product_recombination_postgres.py::test_qualified_feedback_reaches_current_join_or_split_target_and_real_settlement[True-fresh_original_available]",
    "backend/tests/test_product_recombination_postgres.py::test_prejoin_captured_original_is_repeated_after_real_lineage_without_rekey_or_second_reset",
    "backend/tests/test_product_recombination_postgres.py::test_prepared_original_refuses_new_lineage_then_fresh_current_target_settles",
)


# This is a single native owner obligation, not a FAST/planner replacement.
QUEUE_PROOF = "backend/tests/test_study_encounter_intake_boundary.py::test_applied_game_priority_allows_bounded_daily_queue_preparation_without_losing_attempts"


def queue_proof_passed(report):
    """Require the exact case to have passed, not merely a nonempty suite."""
    executed = list(ET.parse(report).getroot().iter("testcase"))
    if not executed or any(
        case.find(tag) is not None
        for case in executed
        for tag in ("skipped", "failure", "error")
    ):
        raise ValueError(
            "Required queue presentation native case is missing, skipped or failed"
        )
    cases = [
        case
        for case in executed
        if case.get("classname", "").endswith("test_study_encounter_intake_boundary")
        and case.get("name") == QUEUE_PROOF.split("::")[1]
    ]
    if len(cases) != 1 or any(
        cases[0].find(tag) is not None for tag in ("skipped", "failure", "error")
    ):
        raise ValueError(
            "Required queue presentation native case is missing, skipped or failed"
        )
    return QUEUE_PROOF


def source_binding(root=ROOT, base="origin/main", suite_id="study-game-priority"):
    """Bind the existing declared suite inputs to actual source bytes and git endpoints."""

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True
        ).stdout

    head = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    resolved_base = git("rev-parse", "--verify", base + "^{commit}").decode().strip()
    merge_base = git("merge-base", head, resolved_base).decode().strip()
    manifest_path = root / "components.json"
    manifest = json.loads(manifest_path.read_text())
    suite = next(item for item in manifest["suites"] if item["id"] == suite_id)
    inputs = ["components.json", *suite["inputs"]]
    tracked = git("ls-files", "-z", "--", *inputs).decode().split("\0")
    files = sorted(path for path in tracked if path)
    if not files:
        raise ValueError("Priority proof has no declared source inputs")
    for selected in inputs:
        if not any(
            path == selected or path.startswith(selected.rstrip("/") + "/")
            for path in files
        ):
            raise ValueError("Missing declared priority input: " + selected)
    hashes = {}
    for path in files:
        file = root / path
        if not file.is_file() or file.is_symlink():
            raise ValueError("Missing or unsupported priority input: " + path)
        hashes[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    return {
        "requestedBase": base,
        "resolvedBase": resolved_base,
        "resolvedHead": head,
        "mergeBase": merge_base,
        "sourceInputs": hashes,
        "worktreeStatus": git("status", "--porcelain", "--untracked-files=normal")
        .decode()
        .splitlines(),
    }


def junit_path(arguments, environment):
    options = [*shlex.split(environment.get("PYTEST_ADDOPTS", "")), *arguments]
    found = []
    for index, option in enumerate(options):
        if option.startswith(("--junitxml=", "--junit-xml=")):
            found.append(option.split("=", 1)[1])
        elif option in ("--junitxml", "--junit-xml") and index + 1 < len(options):
            found.append(options[index + 1])
    return Path(found[-1]) if found else None


def main(arguments=()):
    # Reuse the receiving owner's existing source-overlay qualification inputs;
    # select the caller's already-installed Python, never install dependencies.
    sources = (
        "backend",
        "database",
        "packages/repertoire-comparison/src",
        "packages/game-classification/src",
        "packages/game-ingestion/src",
        "packages/game-review-contracts/src",
        "packages/evidence-math/src",
        "packages/scheduling/src",
        "packages/chess-core/src",
        "services/learning/application/src",
        "services/learning/remediation/src",
        "contracts/game-evidence/src",
        "contracts/analysis-jobs/src",
        "packages/chess-line/src",
        "packages/board-ui/src",
        "packages/game-actions/src",
        "packages/study-session/src",
        "contracts/consumer-examples/src",
    )
    paths = [ROOT / source for source in sources]
    environment = os.environ | {
        "PYTHONPATH": os.pathsep.join(map(str, paths)),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    queue_only = "--queue-presentation-only" in arguments
    arguments = tuple(
        argument for argument in arguments if argument != "--queue-presentation-only"
    )
    suite_id = "study-queue-presentation" if queue_only else "study-game-priority"
    selection = (QUEUE_PROOF,) if queue_only else REQUIRED
    command = [sys.executable, "-m", "pytest", "-q", *selection, *arguments]
    collecting = "--collect-only" in arguments
    lock_path = Path(
        environment.get(
            "TEMPO_VALIDATION_LOCK_PATH", "/tmp/tempo-mac-validation-20261025.lock"
        )
    )

    def execute():
        if collecting:
            return subprocess.run(command, cwd=ROOT, env=environment).returncode
        before = source_binding(
            base=environment.get("TEMPO_PRIORITY_PROOF_BASE", "origin/main"),
            suite_id=suite_id,
        )
        with tempfile.TemporaryDirectory(prefix="tempo-priority-junit-") as temporary:
            report = junit_path(arguments, environment)
            effective_command = command
            if report is None:
                report = Path(temporary) / "cases.xml"
                effective_command = [*command, "--junitxml=" + str(report)]
            elif not report.is_absolute():
                report = ROOT / report
            outcome = subprocess.run(
                effective_command, cwd=ROOT, env=environment
            ).returncode
            if outcome:
                return outcome
            try:
                identity = queue_proof_passed(report)
                after = source_binding(
                    base=environment.get("TEMPO_PRIORITY_PROOF_BASE", "origin/main"),
                    suite_id=suite_id,
                )
                if before != after:
                    raise ValueError(
                        "Priority proof inputs or candidate changed during execution"
                    )
            except (
                OSError,
                ValueError,
                ET.ParseError,
                subprocess.CalledProcessError,
            ) as error:
                print("Priority proof evidence invalid: " + str(error), file=sys.stderr)
                return 1
            evidence = {
                "suite": suite_id,
                "scope": "queue-presentation-native-case",
                "expectedIdentities": [QUEUE_PROOF],
                "executedIdentities": [identity],
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "python": sys.version,
                "platform": platform.platform(),
                "comparison": before,
                "comparisonAfter": after,
            }
            destination = environment.get("TEMPO_PRIORITY_EVIDENCE")
            if destination:
                try:
                    owner_root = Path(
                        environment["TEMPO_PRIORITY_EVIDENCE_ROOT"]
                    ).resolve(strict=True)
                    target = Path(destination).resolve()
                    fixture_root = Path(
                        environment["TEMPO_GAME_FEEDBACK_EVIDENCE_ROOT"]
                    ).resolve(strict=True)
                    if (
                        target.parent != owner_root
                        or fixture_root != owner_root / "fixtures"
                        or target.exists()
                    ):
                        raise ValueError(
                            "Priority evidence requires a fresh owned root"
                        )
                    reports = list(fixture_root.glob("*/fixture.json"))
                    if len(reports) != 1:
                        raise ValueError("Expected one owned PostgreSQL fixture report")
                    fixture = json.loads(reports[0].read_text())
                    if (
                        fixture.get("cleanup_verified") is not True
                        or fixture.get("owner") != reports[0].parent.name
                        or not fixture.get("container_id")
                        or not fixture.get("image_id")
                    ):
                        raise ValueError("Missing owned PostgreSQL identity or cleanup")
                    evidence["fixture"] = {
                        key: fixture[key]
                        for key in (
                            "owner",
                            "container_id",
                            "image_id",
                            "profile",
                            "storage",
                            "memory_bytes",
                            "pids_limit",
                            "cleanup_verified",
                        )
                    }
                    with tempfile.NamedTemporaryFile(
                        mode="w", dir=owner_root, prefix="evidence-", delete=False
                    ) as output:
                        temporary = output.name
                        json.dump(evidence, output, sort_keys=True)
                    os.replace(temporary, target)
                except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                    print(
                        "Priority fixture evidence invalid: " + str(error),
                        file=sys.stderr,
                    )
                    return 1
            else:
                evidence["fixture"] = (
                    "owned postgres:18.6-trixie; actual image and cleanup recorded by fixture"
                )
            print(json.dumps(evidence, sort_keys=True))
            return 0

    if not collecting and lock_path.exists():
        # Never truncate or unlink the shared advisory lock.
        with lock_path.open("r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("VALIDATION_LOCK_BUSY", file=sys.stderr)
                return 2
            try:
                return execute()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return execute()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
