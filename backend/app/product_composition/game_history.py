"""Private retained-library preview and opt-in durable task wiring.

No provider, UI, new PGN, startup worker or live policy choice is required here.
The portable encounter projection and current receiving owner are injected.
"""
from dataclasses import dataclass
from collections import Counter
from datetime import datetime
import json
from typing import Callable

from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_learning_remediation import RemediationError
from tempo_legacy_database.adapters.games.authority import acceptance as source_owner
from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable

from app.games.feedback.comparison import compare_game
from app.games.feedback.history import HistoryComparison, PreparedHistoryPlan, history_task_identity
from app.legacy_game_observation import capture_game
from app.repertoire.interfaces import build_index, project_capture
from .feedback_commands import retained_time_precision
from .retained_game import retained_canonical_game

KIND = "game_feedback_history"


def project_retained_encounters(*, game, provider, snapshot, findings, detected_at, time_precision):
    """Bind the shared pure projector to actual retained canonical source facts."""
    from app.contracts.product_v02.repertoire import IntroductionGameScope
    from app.games.feedback.coordinator import project_encounters
    canonical_provider = {"lichess": "lichess", "chess.com": "chesscom", "chesscom": "chesscom"}.get(game.source_id)
    if canonical_provider is None or provider != canonical_provider:
        raise ValueError("retained_encounter_provider_mismatch")
    if time_precision not in {"unknown", "day", "hour", "minute", "second", "millisecond"}:
        raise ValueError("retained_encounter_time_precision_invalid")
    occurred_at = game.played_at if time_precision in {"second", "millisecond"} else None
    if time_precision in {"second", "millisecond"} and occurred_at is None:
        raise ValueError("retained_encounter_occurrence_missing")
    source = IntroductionGameScope(provider=provider, game_id=game.game_id,
        source_revision=game.source_revision, source_digest=game.source_digest)
    return project_encounters(game, findings=tuple(findings), snapshots=(snapshot,), source=source,
        detected_at=detected_at, occurred_at=occurred_at, payload_digest=game.source_digest)


def prepare_retained_capture(captured, *, detected_at):
    """Use #488's qualified source-domain assembly; do not rebuild its snapshot."""
    from .encounters import prepare_captured_encounters
    return prepare_captured_encounters(captured, detected_at=detected_at)


def current_retained_history_comparer(*, capture=capture_game, prepare_capture=None):
    """Explicit #488 binding; construction performs no read or kickoff."""
    from app.services.retained_history_capture import capture_history_positions
    return RetainedHistoryComparer(prepare_capture=prepare_capture or prepare_retained_capture,
        capture=capture, capture_positions=capture_history_positions if capture is capture_game else None)


def current_retained_history_receiver(*, policy_identity, operation_stopped,
                                      introduction_policy, clock,
                                      receive_admitted=None, prepare_capture=None):
    """Bind the actual admitted receiver and owner policy without a fallback."""
    if not callable(operation_stopped) or not callable(clock) or introduction_policy is None:
        raise ValueError("explicit_history_operation_control_policy_and_clock_required")
    def receive(database, *, capture, snapshot, command):
        operation = receive_admitted
        if operation is None:
            from .encounters import receive_admitted_encounter
            operation = receive_admitted_encounter
        return operation(database, capture=capture, snapshot=snapshot, command=command,
            operation_stopped=operation_stopped, introduction_policy=introduction_policy, clock=clock)
    return RetainedEncounterReceiver(policy_identity, receive=receive,
        prepare_capture=prepare_capture or prepare_retained_capture)


def validate_history_plan_scope(plan, *, selection):
    """Bind a frozen plan to the trusted start scope, before any task write."""
    if plan.selection != selection:
        raise ValueError("retained_history_selection_changed")
    if not 0 <= plan.preview.selected_games <= selection.maximum_games:
        raise ValueError("retained_history_game_bound_invalid")
    if not 0 <= plan.preview.positions_compared <= selection.maximum_positions:
        raise ValueError("retained_history_position_bound_invalid")
    start = datetime.fromisoformat(selection.window.start_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(selection.window.end_at.replace("Z", "+00:00"))
    games, keys = set(), set()
    for command in plan.encounters:
        if (command.key.workspace_id != selection.workspace_id
                or command.key.provider != selection.provider
                or command.source.provider != selection.provider
                or command.key.game_id != command.source.game_id
                or command.decision.repertoire_id not in selection.repertoire_ids
                or command.detected_at != plan.detected_at
                or selection.after_game_id is not None and command.key.game_id <= selection.after_game_id):
            raise ValueError("retained_history_command_outside_selection")
        if command.occurred_at is not None:
            occurred = datetime.fromisoformat(command.occurred_at.replace("Z", "+00:00"))
            if not start <= occurred < end:
                raise ValueError("retained_history_command_outside_window")
        if plan.version >= 2 and not (selection.start_ply <= command.key.occurrence_ply-1
                                      < selection.start_ply+selection.maximum_plies):
            raise ValueError("retained_history_command_outside_ply_window")
        games.add(command.key.game_id)
        identity = command.key.logical_id()
        if identity in keys:
            raise ValueError("retained_history_repeated_command_key")
        keys.add(identity)
    if len(games) > selection.maximum_games:
        raise ValueError("retained_history_game_bound_invalid")


class RetainedHistoryComparer:
    """Reuse #488's authentic retained canonical/capture and #486 comparer.

    Production uses #488's public prepare_capture assembly. The older explicit
    projector seam remains for source/pure tests, with no revision rewriting.
    Neither seam permits an absent finding to be labelled a correct move.
    """
    def __init__(self, *, project_encounters=None, prepare_capture=None, capture=capture_game,
                 capture_positions=None):
        if project_encounters is not None and prepare_capture is not None:
            raise ValueError("one_retained_encounter_preparation_capability_required")
        self.project_encounters, self.capture = project_encounters, capture
        self.prepare_capture = prepare_capture
        self.capture_positions = capture_positions
        self.projection_available = project_encounters is not None or prepare_capture is not None

    def position_bound(self, selection, total_positions):
        if self.capture_positions is None:
            return total_positions
        return max(0, min(total_positions-selection.start_ply, selection.maximum_plies))

    def compare(self, selection, game_id, repertoire_id, detected_at):
        if self.capture_positions is None:
            return self._compare_capture(self.capture(repertoire_id, game_id),
                selection, game_id, repertoire_id, detected_at)
        try:
            selected = self.capture_positions(repertoire_id, game_id, start_ply=selection.start_ply,
                                              maximum_plies=selection.maximum_plies)
        except (CurrentDecisionUnavailable, RemediationError) as error:
            raise ValueError(getattr(error, "reason", None) or str(error)) from None
        findings, encounters = [], []
        unresolved = list(selected.unresolved)
        for ply, captured in selected.captures:
            try:
                result = self._compare_capture(captured, selection, game_id, repertoire_id, detected_at)
            except (ValueError, CurrentDecisionUnavailable, RemediationError) as error:
                unresolved.append((ply, getattr(error, "reason", None) or str(error)))
                continue
            selected_findings = tuple(item for item in result.findings
                if item.ply == ply+1 and item.repertoire_id == repertoire_id)
            findings.extend(selected_findings)
            encounters.extend(item for item in result.encounters if item.key.occurrence_ply == ply+1)
            if any(item.kind in {"known_unavailable", "ambiguous", "conflict"} for item in selected_findings):
                unresolved.append((ply, "retained_position_authority_or_mapping_unresolved"))
            elif not selected_findings:
                unresolved.append((ply, "retained_position_without_reportable_finding"))
        return HistoryComparison(selected.positions, tuple(findings), tuple(encounters), False,
            len({ply for ply, _ in unresolved}), tuple(reason for _, reason in unresolved),
            selected.remaining_positions)

    def _compare_capture(self, captured, selection, game_id, repertoire_id, detected_at):
        try:
            raw = captured["game"]
            provider = {"lichess": "lichess", "chess.com": "chesscom", "chesscom": "chesscom"}.get(raw["provider"])
            game = retained_canonical_game(captured)
            if (provider, game.game_id, game.workspace_id) != (selection.provider, game_id, selection.workspace_id):
                raise ValueError("retained_history_source_scope_mismatch")
            if raw.get("adaptive_excluded"):
                raise ValueError("retained_history_source_now_excluded")
            if self.prepare_capture is not None:
                prepared = self.prepare_capture(captured, detected_at=detected_at)
                if prepared.capture["game"] != raw:
                    raise ValueError("retained_encounter_preparation_changed_source")
                snapshot, findings, encounters = prepared.snapshot, tuple(prepared.findings), tuple(prepared.encounters)
            else:
                snapshot = project_capture(captured, comparison_game=game)
                precision = retained_time_precision(raw)
                findings = compare_game(game, provider=provider, time_precision=precision,
                    snapshot=snapshot, reentry_lookup=build_index(snapshot))
                encounters = ()
        except (CurrentDecisionUnavailable, RemediationError) as error:
            raise ValueError(getattr(error, "reason", None) or str(error)) from None
        if self.project_encounters is not None:
            encounters = tuple(self.project_encounters(game=game, provider=provider, snapshot=snapshot,
                findings=findings, detected_at=detected_at, time_precision=precision))
        for command in encounters:
            if (command.key.workspace_id, command.key.provider, command.key.game_id,
                    command.source.source_revision, command.source.source_digest) != (
                        game.workspace_id, provider, game.game_id, game.source_revision, game.source_digest):
                raise ValueError("retained_encounter_source_scope_mismatch")
            if (command.decision not in snapshot.decision_presentations
                        or command.decision.repertoire_id != repertoire_id
                        or command.detected_at != detected_at
                        or not 1 <= command.key.occurrence_ply <= len(game.moves)
                        or command.played_uci != game.moves[command.key.occurrence_ply-1]):
                raise ValueError("retained_encounter_projection_mismatch")
        return HistoryComparison(len(game.moves), tuple(findings), encounters, matches_complete=False)


@dataclass(frozen=True)
class RetainedEncounterReceiver:
    """Outer wiring supplies #487's current owner with the explicit owner policy.

    Production uses the public admitted receive wrapper, which owns source
    fencing, current introductions and operation controls. The older owner_for
    seam remains explicit for focused tests; no fallback receiver is selected.
    """
    policy_identity: str
    owner_for: Callable | None = None
    receive: Callable | None = None
    prepare_capture: Callable | None = None

    def __post_init__(self):
        if (not isinstance(self.policy_identity, str) or not self.policy_identity
                or sum(callable(item) for item in (self.owner_for, self.receive)) != 1):
            raise ValueError("explicit_retained_encounter_owner_and_policy_required")

    def accept_retained_encounter(self, database, command, captured, snapshot, *, selection):
        from tempo_legacy_database.adapters.games.product_history import assert_history_selection
        if self.receive is not None:
            # The wrapper retains the canonical source locks in this borrowed
            # transaction. Check factual filters under those locks afterwards;
            # a mismatch rolls back its provisional effect and the task cursor.
            receipt = self.receive(database, capture=captured, snapshot=snapshot, command=command)
            assert_history_selection(database, selection, (command.key.game_id,), fence=True)
            return receipt
        raw = captured["game"]
        provider = {"lichess": "lichess", "chess.com": "chesscom", "chesscom": "chesscom"}.get(raw["provider"])
        source_owner.lock_imported_source(database, raw["provider"], raw["provider_game_id"])
        assert_history_selection(database, selection, (command.key.game_id,), fence=True)
        current = source_owner.imported_game(database, raw["id"])
        expected_time = raw["played_at"] if retained_time_precision(raw) in {"second", "millisecond"} else None
        same_time = command.occurred_at is None and expected_time is None
        if command.occurred_at is not None and expected_time is not None:
            same_time = (datetime.fromisoformat(command.occurred_at.replace("Z", "+00:00"))
                         == datetime.fromisoformat(expected_time.replace("Z", "+00:00")))
        if (current is None or dict(current) != raw or command.source.source_digest != fingerprint(raw)
                or command.key.game_id != raw["id"] or command.key.provider != provider
                or command.key.workspace_id != captured["authority"]["workspace_id"] or not same_time):
            raise CurrentDecisionUnavailable("canonical_imported_source_changed")
        return self.owner_for(database, captured, snapshot).accept_encounter(command)


def start_history_in_transaction(database, plan: PreparedHistoryPlan, *, receiver: RetainedEncounterReceiver, selection):
    """Save one frozen bounded manifest using existing durable task storage.

    Same run identity replays its original task/cursor. A different manifest or
    policy cannot overwrite that task. The caller commits before acknowledging.
    The explicit run-once operator claims only KIND; no dispatcher registration or new daemon is needed.
    """
    if receiver is None:
        raise ValueError("current_retained_encounter_receiver_required")
    validate_history_plan_scope(plan, selection=selection)
    plan.require_execution(receiver.policy_identity)
    from tempo_legacy_database.adapters.games.product_history import assert_history_selection, lock_history_run
    from tempo_legacy_database.adapters.durable_tasks import storage
    from app.services.durable_tasks import enqueue_task_in_transaction
    lock_history_run(database, plan.selection.workspace_id, plan.run_id)
    existing = storage.task_by_kind(database, (KIND, plan.task_identity))
    if existing is not None:
        saved = json.loads(existing["payload_json"])
        if saved.get("plan_digest") != plan.digest:
            raise ValueError("retained_history_run_identity_reused")
        return dict(existing)
    assert_history_selection(database, selection, tuple({command.key.game_id for command in plan.encounters}))
    payload = dict(plan=plan.payload(), plan_digest=plan.digest, next_index=0, receipts=[])
    return enqueue_task_in_transaction(database, KIND, plan.task_identity, payload, priority=125)


def execute_history_slice(task, *, write, receiver: RetainedEncounterReceiver,
                          admission, capture=capture_game):
    """Run only inside the supplied actual whole-operation admission guard.

    Successful guard entry must mean admitted; denied, unreadable or unknown
    state must raise/defer. No permissive/default guard or pause inference exists.
    The context spans capture through the final owner/cursor commit.
    """
    if not callable(admission):
        raise ValueError("whole_operation_history_admission_required")
    with admission():
        return _execute_admitted_history_slice(task, write=write, receiver=receiver, capture=capture)


def history_run_status(database, *, workspace_id, run_id):
    """Read processing progress and actual owner deferrals without replaying work."""
    from tempo_legacy_database.adapters.durable_tasks import storage
    row = storage.task_by_kind(database, (KIND, history_task_identity(workspace_id, run_id)))
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    plan = PreparedHistoryPlan.from_payload(payload["plan"])
    validate_history_plan_scope(plan, selection=plan.selection)
    if (plan.selection.workspace_id, plan.run_id, plan.digest) != (workspace_id, run_id, payload["plan_digest"]):
        raise ValueError("retained_history_manifest_changed")
    admissions = Counter(receipt["admission_status"] for receipt in payload["receipts"])
    return dict(run_id=run_id, state=row["state"], policy_identity=plan.policy_identity,
        selection=plan.selection.model_dump(mode="json"),
        processed_encounters=payload["next_index"], planned_encounters=len(plan.encounters),
        admission_counts=dict(admissions), receipts=payload["receipts"],
        preview=plan.preview.model_dump(mode="json"))


def _execute_admitted_history_slice(task, *, write, receiver, capture):
    """One encounter and its actual receipt/cursor share one transaction.

    Capture/replay closes before the writer opens. Uncertain outcomes retry the
    saved original command. Pending/held owner receipts remain truthful receipts;
    run completion means observation processing, never promised Study admission.
    """
    from app.services.durable_tasks import (advance_task_slice_in_transaction,
        complete_task_slice_in_transaction, lock_current_slice)
    if task.get("kind") != KIND or receiver is None:
        raise ValueError("explicit_retained_history_task_and_receiver_required")
    payload = task.get("payload")
    if payload is None:
        payload = json.loads(task["payload_json"])
    plan = PreparedHistoryPlan.from_payload(payload["plan"])
    plan.require_execution(receiver.policy_identity)
    validate_history_plan_scope(plan, selection=plan.selection)
    if payload.get("plan_digest") != plan.digest:
        raise ValueError("retained_history_manifest_changed")
    cursor = payload["next_index"]
    if type(cursor) is not int or not 0 <= cursor <= len(plan.encounters):
        raise ValueError("retained_history_cursor_invalid")
    command = plan.encounters[cursor] if cursor < len(plan.encounters) else None
    captured = snapshot = None
    if command is not None:
        captured = capture(command.decision.repertoire_id, command.key.game_id,
                           (command.key.occurrence_ply-1,))
        if receiver.prepare_capture is not None:
            prepared = receiver.prepare_capture(captured, detected_at=plan.detected_at)
            captured, snapshot = prepared.capture, prepared.snapshot
        else:
            snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
    with write() as database:
        if not lock_current_slice(database, task):
            return "superseded"
        if command is None:
            return "complete" if complete_task_slice_in_transaction(database, task) else "superseded"
        receipt = receiver.accept_retained_encounter(database, command, captured, snapshot, selection=plan.selection)
        if receipt.key != command.key or receipt.source_digest != command.source.source_digest:
            raise ValueError("retained_history_owner_receipt_mismatch")
        # Store the actual EncounterReceipt subtype, including disposition and
        # deferred controls. Nothing is downcast to a lapse receipt.
        next_payload = dict(payload, next_index=cursor+1,
            receipts=[*payload["receipts"], receipt.model_dump(mode="json")])
        if not advance_task_slice_in_transaction(database, task, next_phase="encounters", next_payload=next_payload):
            raise RuntimeError("retained_history_checkpoint_lost")
    return "checkpoint"


def run_history_once(*, receiver, write, admission, maximum_units=16, claim=None):
    """Explicit bounded call; no timer, process, service or automatic kickoff."""
    if type(maximum_units) is not int or not 1 <= maximum_units <= 100:
        raise ValueError("bounded_retained_history_unit_count_required")
    if receiver is None:
        raise ValueError("current_retained_encounter_receiver_required")
    if not callable(admission):
        raise ValueError("whole_operation_history_admission_required")
    if claim is None:
        from app.services.durable_tasks import claim_task
        claim = lambda: claim_task(KIND)
    results = []
    for _ in range(maximum_units):
        with admission():
            task = claim()
            if task is None:
                break
            results.append(_execute_admitted_history_slice(task, write=write, receiver=receiver, capture=capture_game))
    return tuple(results)
