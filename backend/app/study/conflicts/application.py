"""Scoped suspend/restore policy over borrowed Study and Repertoire capabilities."""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Protocol


class ConflictError(ValueError):
    pass


@dataclass(frozen=True)
class SourceFence:
    source_revision: str
    revision: int
    digest: str

    def __post_init__(self):
        if not self.source_revision or not self.digest or self.revision<1:
            raise ConflictError('current_source_fence_required')


@dataclass(frozen=True, order=True)
class Occurrence:
    repertoire_id: str
    route_id: str
    ply: int
    presentation_id: str
    presentation_revision: int

    def __post_init__(self):
        if (not self.repertoire_id or not self.route_id or not self.presentation_id
                or self.ply < 0 or self.presentation_revision < 1):
            raise ConflictError("exact_occurrence_required")

    def identity(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Eligibility:
    occurrence: Occurrence
    eligible: bool
    reason: str | None


@dataclass(frozen=True)
class Assessment:
    fence: SourceFence
    decisions: tuple[Eligibility, ...]


@dataclass(frozen=True)
class ConflictCommand:
    command_id: str
    repertoire_id: str
    expected: SourceFence
    occurrences: tuple[Occurrence, ...]

    def __post_init__(self):
        if (not self.command_id or not self.repertoire_id or not 1 <= len(self.occurrences) <= 16
                or len(set(self.occurrences)) != len(self.occurrences)
                or any(item.repertoire_id != self.repertoire_id for item in self.occurrences)):
            raise ConflictError("bounded_exact_scope_required")

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class ConflictReceipt:
    command_id: str
    status: str
    blocked: tuple[Occurrence, ...]
    restored_queue_ids: tuple[int, ...]
    suspended_queue_ids: tuple[int, ...]
    unproven_queue_ids: tuple[int, ...]


class CurrentConflictAuthority(Protocol):
    """Current Repertoire owner revalidates/fences on the SAME borrowed writer.

    Its existing study_eligibility policy decides conflicts. Busy, incomplete or
    unavailable current source must raise/return an unproved decision, never a
    producer grant. Composition injects this capability without private imports.
    """
    def assess(self, command: ConflictCommand) -> Assessment: ...


class ConflictStore(Protocol):
    def lock(self, command: ConflictCommand) -> None: ...
    def receipt(self, command_id: str) -> tuple[str, ConflictReceipt] | None: ...
    def fence_source(self, command: ConflictCommand) -> None: ...
    def validate_target(self, occurrence: Occurrence) -> None: ...
    def hold(self, occurrence: Occurrence, fence: SourceFence, active: bool, now: datetime) -> None: ...
    def suspend(self, occurrence: Occurrence) -> tuple[tuple[int, ...], tuple[int, ...]]: ...
    def restore(self, occurrence: Occurrence) -> tuple[int, ...]: ...
    def save_receipt(self, digest: str, receipt: ConflictReceipt, now: datetime) -> None: ...


class StudyConflicts:
    def __init__(self, store: ConflictStore, authority: CurrentConflictAuthority, *, clock):
        self.store, self.authority, self.clock = store, authority, clock

    def synchronize(self, command: ConflictCommand) -> ConflictReceipt:
        """One bounded owner transaction; no commit, schedule or review authority."""
        now = self.clock()
        if now.utcoffset() is None:
            raise ConflictError("aware_processing_clock_required")
        self.store.lock(command)
        prior = self.store.receipt(command.command_id)
        if prior:
            if prior[0] != command.digest():
                raise ConflictError("command_identity_reused")
            return prior[1]
        self.store.fence_source(command)
        assessed = self.authority.assess(command)
        if assessed.fence != command.expected:
            raise ConflictError("current_repertoire_source_changed")
        decisions = {item.occurrence:item for item in assessed.decisions}
        if (len(assessed.decisions) != len(command.occurrences)
                or len(decisions) != len(command.occurrences) or set(decisions) != set(command.occurrences)):
            raise ConflictError("current_occurrence_unproved")
        # Unknown/invalid/stale source is not a conflict finding or a grant to
        # restore a target. Validate the whole command before its first mutation.
        for occurrence in command.occurrences:
            decision = decisions[occurrence]
            if ((not decision.eligible and decision.reason != "own_response_conflict")
                    or (decision.eligible and decision.reason is not None)):
                raise ConflictError("current_conflict_eligibility_unproved")
            self.store.validate_target(occurrence)
        blocked, restored, suspended, unproven = [], [], [], []
        for occurrence in sorted(command.occurrences):
            conflict = not decisions[occurrence].eligible
            self.store.hold(occurrence, assessed.fence, conflict, now)
            if conflict:
                changed, unknown = self.store.suspend(occurrence)
                blocked.append(occurrence)
                suspended.extend(changed)
                unproven.extend(unknown)
            else:
                restored.extend(self.store.restore(occurrence))
        receipt = ConflictReceipt(command.command_id,"pending" if unproven else "applied",
            tuple(blocked),tuple(sorted(set(restored)-set(suspended))),tuple(sorted(set(suspended))),
            tuple(sorted(set(unproven))))
        self.store.save_receipt(command.digest(), receipt, now)
        return receipt
