"""Bounded #267 preservation checks through caller-supplied disposable ports.

This module has no database/browser adapter or command-line entry point. It does
not export, convert, import or replay anything on its own. A composition root
supplies the approved payload, independently prepared normalized goldens and an
adapter whose owner verifies and disposes of its exact rehearsal target.

Passing the synthetic adapter tests proves the checker, not a history converter,
PostgreSQL transaction, browser delivery, installed mapping or live cutover.
"""

from copy import deepcopy
import json
from typing import Callable, Literal, Mapping, Protocol

from study_snapshot_compare import SnapshotError, compare_snapshots


ImportOutcome = Literal["complete", "interrupted", "conflict"]
ReplayOutcome = Literal["complete", "blocked"]

LEAF_GROUPS = {
    "content_identity": ("cards", "accepted_definitions"),
    "reviews_schedules": ("reviews", "schedules", "introductions", "accepted_definitions"),
    "server_receipts": ("queue_attempts", "operation_receipts", "introductions"),
    "browser_intents": ("browser_intents",),
}


class RehearsalError(ValueError):
    """A supplied contract or observed preservation check failed.

    Messages contain fixed check names only; records, paths and authority values
    are not included. The caller owns private diagnostic retention.
    """


class DisposableHistoryTarget(Protocol):
    """Semantic operations implemented by the actual migration/replay owner.

    ``disposable`` must be exactly True after the adapter's own target checks.
    ``stop_at`` names a reviewed checkpoint, never an inferred transaction split.
    The adapter returns ``interrupted`` only after unwinding the import; snapshot
    reads must observe durable committed state on an independent connection.
    ``conflict`` means no part of that attempted import changed stored state.
    Replay must use original authority and request identity. An acknowledgement
    alone is insufficient for ``complete``; blocked work remains retained.
    """

    disposable: bool

    def snapshot(self) -> object: ...

    def import_history(self, payload: object, *, stop_at: str | None = None) -> ImportOutcome: ...

    def replay_pending(self) -> ReplayOutcome: ...

    def close(self) -> None: ...


class DisposableHistoryTargets(Protocol):
    """One task-owned target per scenario; never a live-target selector.

    ``open(..., fresh=False)`` reconnects to the same retained target. Its durable
    state must survive ``close``. ``discard`` may remove only the verified target
    allocated for that scenario; no implementation is supplied by this module.
    """

    def open(self, scenario: str, *, fresh: bool) -> DisposableHistoryTarget: ...

    def discard(self, scenario: str) -> None: ...


def _json_copy(value: object) -> object:
    def validate(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for member in item:
                validate(member)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for member in item.values():
                validate(member)
            return
        raise ValueError

    try:
        validate(value)
        json.dumps(value, allow_nan=False)
        return deepcopy(value)
    except (TypeError, ValueError, RecursionError):
        raise RehearsalError("payload_must_be_finite_json") from None


def _same_json(left: object, right: object) -> bool:
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        return False


def convert_history_snapshot(*, workspace_id: str, sources: Mapping[str, object],
                             converters: Mapping[str, Callable],
                             approvals: Mapping[str, Callable[[object], bool]]) -> dict[str, object]:
    """Compose the four exclusive leaf outputs without importing a leaf.

    The caller's per-leaf trusted approval closures bind each whole packet to its
    exact #389 checkpoint. Browser JavaScript may be bridged by the caller's
    callable; this module never selects a process, browser, service or database.
    Shared groups concatenate records; duplicate keys are rejected by #388,
    never silently overwritten or merged. Every packet is approved before any
    converter runs, and a converter may not mutate its supplied packet.
    """
    if type(workspace_id) is not str or not workspace_id:
        raise RehearsalError("workspace_required")
    if any(set(collection) != set(LEAF_GROUPS) for collection in (sources, converters, approvals)):
        raise RehearsalError("all_four_converter_leaves_required")
    packets = {leaf: _json_copy(source) for leaf, source in sources.items()}
    for leaf, source in packets.items():
        if approvals[leaf](deepcopy(source)) is not True:
            raise RehearsalError("approved_whole_source_required")
    snapshot = {
        "schema_version": 1, "workspace_id": workspace_id, "unresolved": [],
        "entities": {group: [] for group in (
            "cards", "reviews", "schedules", "introductions", "accepted_definitions",
            "queue_attempts", "operation_receipts", "browser_intents",
        )},
    }
    for leaf, groups in LEAF_GROUPS.items():
        source = packets[leaf]
        original = _json_copy(source)
        converted = converters[leaf](source, approval=approvals[leaf])
        if not _same_json(source, original):
            raise RehearsalError("converter_mutated_source")
        if type(converted) is not dict or set(converted) != set(groups):
            raise RehearsalError("converter_group_ownership_mismatch")
        for group in groups:
            if type(converted[group]) is not list:
                raise RehearsalError("converter_records_must_be_lists")
            snapshot["entities"][group].extend(_json_copy(converted[group]))
    _validate_snapshot(snapshot)
    return snapshot


def _validate_snapshot(snapshot: object) -> None:
    try:
        compare_snapshots(snapshot, snapshot)
    except (SnapshotError, RecursionError):
        raise RehearsalError("invalid_normalized_snapshot") from None


def _expect_snapshot(target: DisposableHistoryTarget, expected: object, check: str) -> None:
    try:
        equal = compare_snapshots(expected, target.snapshot())["equal"]
    except (SnapshotError, RecursionError):
        raise RehearsalError(check + "_invalid_snapshot") from None
    if equal is not True:
        raise RehearsalError(check + "_mismatch")


def _import(target: DisposableHistoryTarget, payload: object, *, outcome: str,
            stop_at: str | None = None) -> None:
    supplied = _json_copy(payload)
    original = _json_copy(supplied)
    actual = target.import_history(supplied, stop_at=stop_at)
    if not _same_json(supplied, original):
        raise RehearsalError("import_mutated_payload")
    if actual != outcome:
        raise RehearsalError("unexpected_import_outcome")


def _open(targets: DisposableHistoryTargets, scenario: str, *, fresh: bool) -> DisposableHistoryTarget:
    target = targets.open(scenario, fresh=fresh)
    if target.disposable is not True:
        target.close()
        raise RehearsalError("verified_disposable_target_required")
    return target


def rehearse_history(*, targets: DisposableHistoryTargets, payload: object,
                     initial: object, imported: object, replayed: object,
                     interruptions: Mapping[str, object], conflicts: Mapping[str, object],
                     replay_outcome: ReplayOutcome = "complete") -> dict[str, object]:
    """Check supplied preservation goldens, retries, restart and replay.

    Every interruption checkpoint needs its independent expected durable
    snapshot. Conflict payloads are supplied explicitly; this checker never
    chooses identities, changes fields or generates conflicting source data.
    All goldens are validated before opening any target. At least one reviewed
    interruption and one conflict are required. No pass grants cutover or
    real-data acceptance: those remain separate owner/reviewer/operator gates.
    """
    if not interruptions or not conflicts:
        raise RehearsalError("interruption_and_conflict_cases_required")
    if replay_outcome not in ("complete", "blocked"):
        raise RehearsalError("invalid_replay_outcome")
    for cases in (interruptions, conflicts):
        if not isinstance(cases, Mapping) or any(type(name) is not str or not name for name in cases):
            raise RehearsalError("case_names_must_be_nonempty_strings")
    snapshots = [initial, imported, replayed, *interruptions.values()]
    for snapshot in snapshots:
        _validate_snapshot(snapshot)
    workspace = initial["workspace_id"]
    if any(snapshot["workspace_id"] != workspace for snapshot in snapshots):
        raise RehearsalError("golden_workspace_mismatch")
    payload = _json_copy(payload)
    conflicts = {name: _json_copy(value) for name, value in conflicts.items()}
    checks = []

    scenario = "repeat-and-replay"
    target = None
    verified = False
    try:
        target = _open(targets, scenario, fresh=True)
        verified = True
        _expect_snapshot(target, initial, "initial")
        _import(target, payload, outcome="complete")
        _expect_snapshot(target, imported, "first_import")
        _import(target, payload, outcome="complete")
        _expect_snapshot(target, imported, "repeated_import")
        checks.extend(["first_import", "repeated_import"])
        for check in ("first_replay", "repeated_replay"):
            if target.replay_pending() != replay_outcome:
                raise RehearsalError(check + "_unexpected_outcome")
            _expect_snapshot(target, replayed, check)
            checks.append(check)
    finally:
        if target is not None:
            target.close()
        if verified:
            targets.discard(scenario)

    for number, (checkpoint, interrupted) in enumerate(interruptions.items()):
        scenario = "interruption-" + str(number)
        target = None
        verified = False
        try:
            target = _open(targets, scenario, fresh=True)
            verified = True
            _expect_snapshot(target, initial, "interruption_initial")
            _import(target, payload, outcome="interrupted", stop_at=checkpoint)
            target.close()
            target = None
            target = _open(targets, scenario, fresh=False)
            _expect_snapshot(target, interrupted, "interrupted_durable_state")
            _import(target, payload, outcome="complete")
            _expect_snapshot(target, imported, "resumed_import")
            _import(target, payload, outcome="complete")
            _expect_snapshot(target, imported, "repeated_resumed_import")
            checks.append("interruption_resume_" + str(number))
        finally:
            if target is not None:
                target.close()
            if verified:
                targets.discard(scenario)

    for number, conflict in enumerate(conflicts.values()):
        scenario = "conflict-" + str(number)
        target = None
        verified = False
        try:
            target = _open(targets, scenario, fresh=True)
            verified = True
            _expect_snapshot(target, initial, "conflict_initial")
            _import(target, payload, outcome="complete")
            _expect_snapshot(target, imported, "conflict_baseline")
            for _ in range(2):
                _import(target, conflict, outcome="conflict")
                _expect_snapshot(target, imported, "conflict_preservation")
            checks.append("conflict_rejection_" + str(number))
        finally:
            if target is not None:
                target.close()
            if verified:
                targets.discard(scenario)

    return {
        "scope": "supplied-disposable-port-rehearsal",
        "checks": checks,
        "real_data_acceptance": False,
        "cutover_authorized": False,
    }
