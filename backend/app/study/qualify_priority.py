"""Required affected Games→Study boundary selection, using the existing pytest runner.

Run from any directory: python backend/app/study/qualify_priority.py.
The joined #618–#624 source owns the explicit finite selection below. Missing
required files/cases fail pytest collection; there are no optional fallbacks.
Packaged/browser proof remains the separate explicit games-collection-release suite.
"""
from pathlib import Path
import fcntl
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
