"""One bounded import page or owner-command delivery. No infrastructure imports."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable, Mapping

from app.contracts.product_v02.games import CanonicalGame, Finding, FirstDepartureSelection
from app.study.interfaces.game_priority import EncounterFeedbackPort, EncounterReceipt, GameEffectControl, GameEffectKey, GameEncounter, GameLapse, LearningFeedbackPort
from app.contracts.product_v02.repertoire import IntroductionGameScope, RepertoireSnapshot, RepertoireSnapshotPort

from .ports import (Acquisition, AcquisitionFailure, Capture, Clock, Comparer, Delivery,
                    GamesStore, JobState, Normalizer, PageCommit, SourceIdentity, SourceState)
from .first_boundary import select_first_boundary


def stable_id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# Only explicit exclusions emitted by the current provider adapters are safe.
_SAFE_REJECTIONS = {
    "lichess": frozenset({"filtered_speed", "filtered_rated", "unsupported_variant", "duplicate_source_id", "unfinished_game"}),
    "chesscom": frozenset({"outside_window", "time_control_filtered", "rated_filtered",
                           "unsupported_variant", "duplicate_source_id", "unfinished_game"}),
}
def _comparison_revision(snapshots: tuple[RepertoireSnapshot, ...], unavailable: Iterable[str]) -> str:
    """Capture clocks are provenance, not new material comparison facts."""
    documents = []
    for snapshot in snapshots:
        document = snapshot.model_dump(mode="json")
        membership = document["membership"]
        membership.pop("captured_at")
        membership["scope_repertoire_ids"].sort()
        for route in membership["routes"]:
            route["reentries"].sort(key=lambda value: json.dumps(value, sort_keys=True))
        for owner, field in ((membership, "routes"), (document, "boundaries"),
                             (document, "decision_presentations")):
            owner[field].sort(key=lambda value: json.dumps(value, sort_keys=True))
        documents.append(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return stable_id("comparison-facts-v2-first-departure", sorted(set(documents)), sorted(unavailable))


def source_revision(command: GameLapse | GameEncounter | GameEffectControl) -> int:
    if isinstance(command, GameLapse):
        return command.finding.source_revision
    return command.source.source_revision if isinstance(command, GameEncounter) else command.source_revision


def delivery_for(source_key: str, command: GameLapse | GameEncounter | GameEffectControl) -> Delivery:
    kind = "lapse" if isinstance(command, GameLapse) else ("encounter" if isinstance(command, GameEncounter) else command.action)
    return Delivery(operation_id=stable_id(kind, command.key.logical_id(), source_revision(command)), source_key=source_key,
                    lapse=command if isinstance(command, GameLapse) else None,
                    encounter=command if isinstance(command, GameEncounter) else None,
                    control=command if isinstance(command, GameEffectControl) else None)


def encounter_observation(encounter: GameEncounter) -> tuple:
    """Admission/review state is not a new historical source observation."""
    return (encounter.key.logical_id(), encounter.source.source_digest, encounter.decision.position_key,
            encounter.expected_uci, encounter.played_uci)


def project_encounters(game: CanonicalGame, *, findings: tuple[Finding, ...],
                       snapshots: tuple[RepertoireSnapshot, ...], source: IntroductionGameScope,
                       detected_at: str, occurred_at: str | None,
                       payload_digest: str | None = None,
                       selected_repertoire_ids: Iterable[str] | None = None,
                       unavailable_repertoire_ids: Iterable[str] = ()) -> tuple[GameEncounter, ...]:
    """Project only the exact first learner departure; findings stay diagnostic.

    Original occurrence/detection/source facts are explicit; no PGN, acquisition,
    clock, store, receipt or admission policy is consulted. An empty result is not
    evidence of complete/correct coverage: callers retain unresolved findings.
    """
    if (source.game_id, source.source_revision, source.source_digest) != (
            game.game_id, game.source_revision, game.source_digest):
        raise ValueError("encounter source differs from canonical game")
    if any(snapshot.membership.workspace_id != game.workspace_id for snapshot in snapshots):
        raise ValueError("encounter snapshot workspace differs from canonical game")
    for finding in findings:
        if (finding.provider, finding.game_id, finding.source_revision, finding.source_digest) != (
                source.provider, source.game_id, source.source_revision, source.source_digest):
            raise ValueError("encounter finding differs from supplied source")
        if not any(finding.repertoire_id in snapshot.membership.scope_repertoire_ids
                   and finding.repertoire_revision == snapshot.membership.revision for snapshot in snapshots):
            raise ValueError("encounter finding is outside supplied snapshot scope")
        if finding.ply > len(game.moves) or finding.played_uci != game.moves[finding.ply - 1]:
            raise ValueError("encounter played move differs from captured game")
    try:
        selection = select_first_boundary(game, snapshots=snapshots, source=source,
            selected_repertoire_ids=selected_repertoire_ids,
            unavailable_repertoire_ids=unavailable_repertoire_ids)
    except ValueError:
        return ()  # Malformed authored facts cannot authorize an owner command.
    return _encounter_for_selection(selection, detected_at=detected_at,
        occurred_at=occurred_at, payload_digest=payload_digest)


def _encounter_for_selection(selection: FirstDepartureSelection, *, detected_at: str,
                             occurred_at: str | None, payload_digest: str | None) -> tuple[GameEncounter, ...]:
    if selection.status != "own_actionable":
        return ()
    decision, source = selection.decision, selection.source
    if decision.introduction_game is not None and decision.introduction_game != source:
        # A new command cannot assert another game's chronology. Keep the
        # captured selection intact and omit only this inapplicable old proof.
        decision = decision.model_copy(update={"introduced_before_game": None, "introduction_game": None})
    return (GameEncounter(key=GameEffectKey(workspace_id=selection.workspace_id,
        provider=source.provider, game_id=source.game_id, decision_id=decision.decision_id,
        occurrence_ply=selection.ply), source=source, decision=decision,
        expected_uci=selection.expected_uci[0], played_uci=selection.played_uci, observation="missed",
        detected_at=detected_at, occurred_at=occurred_at,
        payload_digest=source.source_digest if payload_digest is None else payload_digest),)


class Coordinator:
    def __init__(self, *, providers: Mapping[str, Acquisition], normalizer: Normalizer,
                 comparer: Comparer, snapshots: RepertoireSnapshotPort, store: GamesStore,
                 learning: LearningFeedbackPort | EncounterFeedbackPort | None, clock: Clock,
                 source_identity: SourceIdentity | None = None,
                 encounter_feedback: bool = False, max_page_records: int = 100,
                 max_page_bytes: int = 8 * 1024 * 1024, max_page_positions: int = 10000):
        if not 1 <= max_page_records <= 1000 or not 1 <= max_page_bytes <= 8 * 1024 * 1024:
            raise ValueError("page bounds exceed optional workflow budget")
        if not 1 <= max_page_positions <= 100000:
            raise ValueError("position batch exceeds optional workflow budget")
        self.encounter_feedback = encounter_feedback
        self.max_page_positions = max_page_positions
        self.providers, self.normalizer, self.comparer = providers, normalizer, comparer
        self.source_identity = source_identity
        self.snapshots, self.store, self.learning, self.clock = snapshots, store, learning, clock
        self.max_page_records, self.max_page_bytes = max_page_records, max_page_bytes

    def capture_page(self, job: JobState, *, stopped=lambda: False) -> None:
        spec = job.spec
        if stopped():
            self.store.save_job(job.model_copy(update={"status": "queued", "lease_until": 0}))
            return
        page = self.providers[spec.provider].fetch_page(account=spec.account, window=spec.window,
                                                       continuation=job.continuation)
        # Revalidate mutable nested data without losing provider subtype provenance.
        page = type(page).model_validate(page.model_dump(mode="python"))
        if (page.provider, page.account, page.window) != (spec.provider, spec.account, spec.window):
            raise AcquisitionFailure("provider_page_scope_mismatch", retryable=False)
        if len(page.records) > self.max_page_records or sum(len(r.pgn.encode()) for r in page.records) > self.max_page_bytes:
            raise AcquisitionFailure("provider_page_exceeds_budget", retryable=False)
        page_json = page.model_dump_json()  # Preserve provider subtype fields before base projection.
        if len(page_json.encode()) > 2 * self.max_page_bytes:
            raise AcquisitionFailure("provider_page_provenance_exceeds_budget", retryable=False)
        if spec.filters and tuple(getattr(page, "filters", ())) != spec.filters:
            raise AcquisitionFailure("provider_page_filter_mismatch", retryable=False)
        snapshots, unavailable_snapshots = [], set()
        snapshot_integrity_error = False
        for repertoire_id in spec.repertoire_ids:
            try:
                supplied = self.snapshots.snapshot(repertoire_id)
            except Exception as error:
                # Optional reads can fail independently of valid acquisition.
                unavailable_snapshots.add(repertoire_id)
                if str(error) in {"snapshot_scope_mismatch", "repertoire_snapshot_scope_mismatch"}:
                    snapshot_integrity_error = True
                continue
            try:
                snapshot = RepertoireSnapshot.model_validate(supplied.model_dump(mode="python"))
                if (snapshot.membership.workspace_id != spec.workspace_id
                        or repertoire_id not in snapshot.membership.scope_repertoire_ids):
                    raise ValueError("snapshot_scope_mismatch")
            except ValueError:
                snapshot_integrity_error = True
                unavailable_snapshots.add(repertoire_id)
                continue
            snapshots.append(snapshot)
        sources, captures, deliveries = [], [], []
        boundary = any(rejection.reason not in _SAFE_REJECTIONS[spec.provider]
                       for rejection in page.rejections)
        comparison_unavailable = False
        quarantined = False
        positions = 0
        for record in page.records:
            if stopped():
                self.store.save_job(job.model_copy(update={"status": "queued", "lease_until": 0}))
                return  # No checkpoint or partial page has been published.
            key = stable_id(spec.workspace_id, spec.provider, record.source_game_id)
            previous = self.store.source(key)
            normalized, findings, encounters, diagnostic = None, (), (), None
            first_departure = None
            compatible = ()
            unresolved = set(unavailable_snapshots)
            integrity_error = snapshot_integrity_error
            try:
                normalized = self.normalizer.normalize(record, spec)
                if (normalized.workspace_id, normalized.source_revision, normalized.source_digest) != (
                        spec.workspace_id, record.source_revision, record.source_digest):
                    raise ValueError("normalization provenance differs from captured source")
                if self.source_identity is not None:
                    verified = normalized
                    normalized = self.source_identity.bind_source(verified, record=record, spec=spec)
                    if (normalized.workspace_id, normalized.starting_fen, normalized.moves,
                            normalized.learner_color) != (verified.workspace_id, verified.starting_fen,
                            verified.moves, verified.learner_color):
                        raise ValueError("canonical source differs from verified history")
                    def instant(value):
                        return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if instant(normalized.played_at) != instant(verified.played_at):
                        raise ValueError("canonical source differs from verified occurrence")
                positions += len(normalized.moves)
                if positions > self.max_page_positions:
                    raise AcquisitionFailure("page_position_budget_exceeded", retryable=False)
            except ValueError:
                integrity_error = True
            if normalized is not None and not integrity_error:
                compatible = tuple(snapshot for snapshot in snapshots
                                   if snapshot.learner_color == normalized.learner_color)
                compared_findings = []
                for snapshot in compatible:
                    repertoire_ids = set(snapshot.membership.scope_repertoire_ids)
                    try:
                        result = tuple(self.comparer.compare(normalized, snapshot, record=record, spec=spec))
                    except Exception:
                        unresolved.update(repertoire_ids)
                        continue
                    if len(result) > 1000:
                        raise AcquisitionFailure("comparison_finding_budget_exceeded", retryable=False)
                    try:
                        for supplied_finding in result:
                            finding = Finding.model_validate(supplied_finding.model_dump(mode="python"))
                            if (finding.provider, finding.game_id, finding.source_revision, finding.source_digest) != (
                                    spec.provider, normalized.game_id, normalized.source_revision, normalized.source_digest):
                                raise ValueError("comparison provenance differs from captured source")
                            if (finding.repertoire_id not in spec.repertoire_ids
                                    or finding.repertoire_id not in repertoire_ids
                                    or finding.repertoire_revision != snapshot.membership.revision):
                                raise ValueError("comparison repertoire scope differs from captured snapshot")
                            compared_findings.append(finding)
                            if finding.diagnostic == "snapshot_incomplete_or_unavailable":
                                unresolved.add(finding.repertoire_id)
                    except ValueError:
                        integrity_error = True
                findings = tuple(sorted(set(compared_findings),
                    key=lambda finding: (finding.ply, finding.repertoire_id, finding.model_dump_json())))
                if not integrity_error:
                    try:
                        first_departure = select_first_boundary(normalized, snapshots=snapshots,
                            source=IntroductionGameScope(provider=spec.provider, game_id=normalized.game_id,
                                source_revision=normalized.source_revision, source_digest=normalized.source_digest),
                            selected_repertoire_ids=spec.repertoire_ids, unavailable_repertoire_ids=unresolved)
                        if self.encounter_feedback:
                            encounters = _encounter_for_selection(first_departure, detected_at=self.clock.utc_now(),
                                occurred_at=(normalized.played_at if record.time_precision in {"second", "millisecond"} else None),
                                payload_digest=normalized.source_digest)
                    except ValueError:
                        integrity_error = True
            if integrity_error:
                diagnostic = "unresolved_parsing_or_comparison_boundary"
                boundary = True
            elif unresolved:
                diagnostic = "comparison_unavailable:" + json.dumps(sorted(unresolved), separators=(",", ":"))
            elif not compatible:
                diagnostic = "no_compatible_repertoire"
            comparison_unavailable = comparison_unavailable or bool(unresolved) or not compatible
            comparison_revision = _comparison_revision(compatible, unresolved)
            fingerprint = stable_id(record.model_dump(mode="json", exclude={"source_revision"}))
            changed = previous is not None and fingerprint != previous.record_fingerprint
            if self.source_identity is not None and previous and previous.normalized is not None and normalized is not None:
                changed = changed or (previous.normalized.game_id, previous.normalized.source_revision,
                    previous.normalized.source_digest) != (normalized.game_id,
                    normalized.source_revision, normalized.source_digest)
            held = changed or bool(previous and previous.quarantined) or integrity_error
            determined_at = previous.determined_at if previous else None
            effects = list(previous.effects) if previous else []
            confirmed = {(f.decision_id, f.ply) for f in findings if f.kind == "confirmed_miss"}
            observations = {}
            for item in encounters:
                observations.setdefault(item.key.logical_id(), set()).add(encounter_observation(item))
            compared_repertoires = {repertoire for snapshot in compatible
                for repertoire in snapshot.membership.scope_repertoire_ids} - unresolved
            for effect in effects:
                if isinstance(effect, GameLapse):
                    still_supported = (effect.finding.repertoire_id not in compared_repertoires
                        or (effect.key.decision_id, effect.key.occurrence_ply) in confirmed)
                else:
                    current = observations.get(effect.key.logical_id())
                    # Later admission/learning or missing proof cannot erase history.
                    # A positively changed observation still fences pending work.
                    still_supported = current is None or encounter_observation(effect) in current
                if held or not still_supported:
                    command = GameEffectControl(key=effect.key, source_revision=source_revision(effect),
                        action="hold", reason="source_corrected" if changed else "reanalysis")
                    deliveries.append(delivery_for(key, command))
                    held = True
            if not held:
                prior_plies = {effect.key.occurrence_ply for effect in effects}
                occupied_keys = {(effect.key.decision_id, effect.key.occurrence_ply) for effect in effects}
                # Encounter intake receives facts; Study owns even a confirmed miss's disposition.
                for finding in findings:
                    if self.encounter_feedback or finding.kind != "confirmed_miss" or (finding.decision_id, finding.ply) in occupied_keys or finding.ply in prior_plies:
                        continue
                    if first_departure is None or first_departure.status != "own_actionable":
                        continue
                    selected = first_departure.decision
                    if (finding.ply, finding.position_key, finding.expected_uci, finding.played_uci,
                            finding.decision_id, finding.presentation_id, finding.presentation_revision) != (
                            first_departure.ply, first_departure.position_key, first_departure.expected_uci,
                            first_departure.played_uci, selected.decision_id, selected.presentation_id,
                            selected.presentation_revision):
                        continue
                    effect_key = GameEffectKey(workspace_id=spec.workspace_id, provider=spec.provider,
                        game_id=finding.game_id, decision_id=finding.decision_id, occurrence_ply=finding.ply)
                    lapse = GameLapse(key=effect_key, finding=finding, detected_at=self.clock.utc_now(),
                        occurred_at=normalized.played_at, payload_digest=normalized.source_digest)
                    effects.append(lapse)
                    occupied_keys.add((finding.decision_id, finding.ply))
                    deliveries.append(delivery_for(key, lapse))
                for encounter in encounters:
                    occurrence = (encounter.key.decision_id, encounter.key.occurrence_ply)
                    if occurrence in occupied_keys or encounter.key.occurrence_ply in prior_plies:
                        continue
                    effects.append(encounter)
                    occupied_keys.add(occurrence)
                    deliveries.append(delivery_for(key, encounter))
            if held and not integrity_error:
                diagnostic = "changed_source_requires_D08_resolution"
            if (not held and first_departure is not None and first_departure.status == "own_actionable"
                    and determined_at is None):
                determined_at = self.clock.utc_now()
            sources.append(SourceState(source_key=key, version=previous.version if previous else 0,
                original=previous.original if previous else record,
                record_fingerprint=previous.record_fingerprint if previous else fingerprint,
                normalized=previous.normalized if changed else normalized, findings=findings,
                comparison_revision=comparison_revision, effects=tuple(effects), encounters=encounters,
                first_departure=previous.first_departure if changed else first_departure,
                determined_at=determined_at, quarantined=held, diagnostic=diagnostic))
            captures.append(Capture(source_key=key, account=spec.account, record=record, provider_record_json=record.model_dump_json(), normalized=normalized,
                findings=findings, encounters=encounters, first_departure=first_departure,
                determined_at=None if changed else determined_at,
                comparison_revision=comparison_revision, diagnostic=diagnostic))
            quarantined = quarantined or held
        resumable = page.continuation is not None and page.continuation != job.continuation
        safe = not boundary and (page.complete or resumable)
        complete = page.complete and safe
        status = "complete" if complete else "queued"
        if boundary or (not complete and not resumable) or (complete and quarantined):
            status = "quarantined"
        error = "unresolved_page_boundary" if boundary else (
            "incomplete_window_without_safe_continuation" if not complete and not resumable else (
                "changed_source_requires_D08_resolution" if quarantined else
                "comparison_unavailable" if comparison_unavailable else None))
        committed_job = job.model_copy(update={"status": status, "continuation": page.continuation if safe else job.continuation,
            "window_complete": complete, "last_checkpoint_at": self.clock.utc_now() if safe else job.last_checkpoint_at,
            "pages_committed": job.pages_committed + 1, "next_attempt_at": 0, "lease_until": 0,
            "error": error, "rejections": page.rejections})
        self.store.commit_page(PageCommit(job=committed_job, sources=tuple(sources), captures=tuple(captures), deliveries=tuple(deliveries),
            provider_page_json=page_json, input_continuation=job.continuation))

    def deliver(self, delivery: Delivery) -> None:
        """Persist the owner receipt; transport success alone is never evidence."""
        command = delivery.command
        try:
            if delivery.control is not None:
                receipt = self.learning.control_effect(command)
            elif delivery.encounter is not None:
                receipt = self.learning.accept_encounter(command)
                if not isinstance(receipt, EncounterReceipt) or (receipt.source_digest, receipt.observation) != (
                        command.source.source_digest, command.observation):
                    raise ValueError("receiver_encounter_receipt_provenance_mismatch")
            else:
                receipt = self.learning.accept_lapse(command)
            revision = source_revision(command)
            if receipt.key != command.key or receipt.source_revision != revision or not receipt.owner_receipt_id:
                raise ValueError("receiver_receipt_identity_mismatch")
            state = "failed" if receipt.outcome in {"conflict", "stale_revision"} else "acknowledged"
            self.store.save_delivery(delivery.model_copy(update={"state": state, "receipt": receipt,
                "attempts": delivery.attempts + 1, "error": receipt.reason if state == "failed" else None}))
        except Exception as error:
            # A receiver may already have committed. Preserve this exact command.
            delay = max(min(300, 2 ** min(delivery.attempts + 1, 8)), getattr(error, "retry_after", 0))
            self.store.save_delivery(delivery.model_copy(update={"attempts": delivery.attempts + 1,
                "next_attempt_at": self.clock.now() + delay, "error": "owner_acknowledgment_uncertain"}))

    def withdraw_source(self, job: JobState, source_key: str) -> None:
        """Durably quarantine an explicitly deleted/excluded source and fence its effects.

        Caller supplies its claimed Games job; source and commands share the same
        transaction as that job's status. This never changes the learning history.
        """
        source = self.store.source(source_key)
        if source is None:
            raise ValueError("source was not captured")
        if source_key != stable_id(job.spec.workspace_id, job.spec.provider, source.original.source_game_id):
            raise ValueError("withdrawal source is outside this job scope")
        controls = tuple(delivery_for(source_key, GameEffectControl(key=effect.key,
            source_revision=source_revision(effect), action="withdraw",
            reason="source_deleted")) for effect in source.effects)
        quarantined = source.model_copy(update={"quarantined": True,
            "diagnostic": "withdrawn_source_requires_D08_resolution"})
        status = job.model_copy(update={"status": "quarantined", "lease_until": 0,
            "error": "withdrawn_source_requires_D08_resolution"})
        self.store.commit_page(PageCommit(job=status, sources=(quarantined,), captures=(), deliveries=controls))
