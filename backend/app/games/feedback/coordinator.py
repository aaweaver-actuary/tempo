"""One bounded import page or owner-command delivery. No infrastructure imports."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping

from tempo_game_classification import resolve_equivalent_mapping

from app.contracts.product_v02.games import CanonicalGame, Finding
from app.study.interfaces.game_priority import EncounterFeedbackPort, EncounterReceipt, GameEffectControl, GameEffectKey, GameEncounter, GameLapse, LearningFeedbackPort
from app.contracts.product_v02.repertoire import IntroductionGameScope, RepertoireSnapshot, RepertoireSnapshotPort

from .ports import (Acquisition, AcquisitionFailure, Capture, Clock, Comparer, Delivery,
                    GamesStore, JobState, Normalizer, PageCommit, SourceIdentity, SourceState)


def stable_id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


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
                       payload_digest: str | None = None) -> tuple[GameEncounter, ...]:
    """Pure shared-command projection for imported canonical history or new pages.

    Original occurrence/detection/source facts are explicit; no PGN, acquisition,
    clock, store, receipt or admission policy is consulted. An empty result is not
    evidence of complete/correct coverage: callers retain unresolved findings.
    """
    if (source.game_id, source.source_revision, source.source_digest) != (
            game.game_id, game.source_revision, game.source_digest):
        raise ValueError("encounter source differs from canonical game")
    if any(snapshot.membership.workspace_id != game.workspace_id for snapshot in snapshots):
        raise ValueError("encounter snapshot workspace differs from canonical game")
    candidates = []
    for finding in findings:
        if (finding.provider, finding.game_id, finding.source_revision, finding.source_digest) != (
                source.provider, source.game_id, source.source_revision, source.source_digest):
            raise ValueError("encounter finding differs from supplied source")
        if not any(finding.repertoire_id in snapshot.membership.scope_repertoire_ids
                   and finding.repertoire_revision == snapshot.membership.revision for snapshot in snapshots):
            raise ValueError("encounter finding is outside supplied snapshot scope")
        # Chronology limits lapse eligibility, not relevance of an exact encounter.
        chronology_only = (finding.kind == "known_unavailable" and finding.diagnostic in {
            "introduction_evidence_unavailable", "game_occurrence_time_not_exact",
            "chronology_requires_timezone", "conflicting_introduction_chronology",
        })
        if ((finding.kind not in {"known_unintroduced", "confirmed_miss"} and not chronology_only)
                or finding.membership != "present" or len(finding.expected_uci) != 1):
            continue
        mappings = {mapping.model_dump_json(): mapping for snapshot in snapshots
            for mapping in snapshot.decision_presentations
            if (mapping.repertoire_id, mapping.repertoire_revision,
                mapping.decision_id, mapping.position_key, mapping.selected_response_uci,
                mapping.presentation_id, mapping.presentation_revision) == (
                finding.repertoire_id, finding.repertoire_revision, finding.decision_id,
                finding.position_key, finding.expected_uci[0], finding.presentation_id,
                finding.presentation_revision)}
        decision = resolve_equivalent_mapping(mappings.values())
        if decision is None or not decision.available:
            continue  # Retain conflicting facts; equivalent route provenance is one target.
        if finding.ply > len(game.moves) or finding.played_uci != game.moves[finding.ply - 1]:
            raise ValueError("encounter played move differs from captured game")
        candidates.append(GameEncounter(key=GameEffectKey(workspace_id=game.workspace_id,
            provider=source.provider, game_id=game.game_id, decision_id=decision.decision_id,
            occurrence_ply=finding.ply), source=source, decision=decision,
            expected_uci=finding.expected_uci[0], played_uci=finding.played_uci,
            observation="matched" if finding.played_uci == finding.expected_uci[0] else "missed",
            detected_at=detected_at, occurred_at=occurred_at,
            payload_digest=source.source_digest if payload_digest is None else payload_digest))
    return tuple(candidates)


class Coordinator:
    def __init__(self, *, providers: Mapping[str, Acquisition], normalizer: Normalizer,
                 comparer: Comparer, snapshots: RepertoireSnapshotPort, store: GamesStore,
                 learning: LearningFeedbackPort | EncounterFeedbackPort, clock: Clock,
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
        if (page.provider, page.account, page.window) != (spec.provider, spec.account, spec.window):
            raise AcquisitionFailure("provider_page_scope_mismatch", retryable=False)
        if len(page.records) > self.max_page_records or sum(len(r.pgn.encode()) for r in page.records) > self.max_page_bytes:
            raise AcquisitionFailure("provider_page_exceeds_budget", retryable=False)
        page_json = page.model_dump_json()  # Preserve provider subtype fields before base projection.
        if len(page_json.encode()) > 2 * self.max_page_bytes:
            raise AcquisitionFailure("provider_page_provenance_exceeds_budget", retryable=False)
        if spec.filters and tuple(getattr(page, "filters", ())) != spec.filters:
            raise AcquisitionFailure("provider_page_filter_mismatch", retryable=False)
        snapshots = tuple(self.snapshots.snapshot(item) for item in spec.repertoire_ids)
        for repertoire_id, snapshot in zip(spec.repertoire_ids, snapshots):
            if snapshot.membership.workspace_id != spec.workspace_id or repertoire_id not in snapshot.membership.scope_repertoire_ids:
                raise AcquisitionFailure("snapshot_scope_mismatch", retryable=False)
        comparison_revision = stable_id(*(s.model_dump(mode="json") for s in snapshots))
        sources, captures, deliveries = [], [], []
        boundary = bool(page.rejections)
        quarantined = False
        positions = 0
        for record in page.records:
            if stopped():
                self.store.save_job(job.model_copy(update={"status": "queued", "lease_until": 0}))
                return  # No checkpoint or partial page has been published.
            key = stable_id(spec.workspace_id, spec.provider, record.source_game_id)
            previous = self.store.source(key)
            normalized, findings, encounters, diagnostic = None, (), (), None
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
                findings = tuple(f for snapshot in snapshots for f in self.comparer.compare(normalized, snapshot, record=record, spec=spec))
                if len(findings) > 1000 * len(snapshots):
                    raise AcquisitionFailure("comparison_finding_budget_exceeded", retryable=False)
                for finding in findings:
                    if (finding.provider, finding.game_id, finding.source_revision, finding.source_digest) != (
                            spec.provider, normalized.game_id, normalized.source_revision, normalized.source_digest):
                        raise ValueError("comparison provenance differs from captured source")
                    if finding.repertoire_id not in spec.repertoire_ids or not any(
                            finding.repertoire_id in snapshot.membership.scope_repertoire_ids
                            and finding.repertoire_revision == snapshot.membership.revision for snapshot in snapshots):
                        raise ValueError("comparison repertoire scope differs from captured snapshot")
                if self.encounter_feedback:
                    encounters = project_encounters(normalized, findings=findings, snapshots=snapshots,
                        source=IntroductionGameScope(provider=spec.provider, game_id=normalized.game_id,
                            source_revision=normalized.source_revision, source_digest=normalized.source_digest),
                        detected_at=self.clock.utc_now(), occurred_at=(normalized.played_at
                            if record.time_precision in {"second", "millisecond"} else None),
                        payload_digest=normalized.source_digest)
            except ValueError:
                diagnostic = "unresolved_parsing_or_comparison_boundary"
                boundary = True
            fingerprint = stable_id(record.model_dump(mode="json", exclude={"source_revision"}))
            changed = previous is not None and fingerprint != previous.record_fingerprint
            if self.source_identity is not None and previous and previous.normalized is not None and normalized is not None:
                changed = changed or (previous.normalized.game_id, previous.normalized.source_revision,
                    previous.normalized.source_digest) != (normalized.game_id,
                    normalized.source_revision, normalized.source_digest)
            held = changed or bool(previous and previous.quarantined) or diagnostic is not None
            effects = list(previous.effects) if previous else []
            confirmed = {(f.decision_id, f.ply) for f in findings if f.kind == "confirmed_miss"}
            observations = {}
            for item in encounters:
                observations.setdefault(item.key.logical_id(), set()).add(encounter_observation(item))
            for effect in effects:
                if isinstance(effect, GameLapse):
                    still_supported = (effect.key.decision_id, effect.key.occurrence_ply) in confirmed
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
            diagnostic = diagnostic or ("changed_source_requires_D08_resolution" if held else None)
            sources.append(SourceState(source_key=key, version=previous.version if previous else 0,
                original=previous.original if previous else record,
                record_fingerprint=previous.record_fingerprint if previous else fingerprint,
                normalized=previous.normalized if changed else normalized, findings=findings,
                comparison_revision=comparison_revision, effects=tuple(effects), encounters=encounters, quarantined=held, diagnostic=diagnostic))
            captures.append(Capture(source_key=key, account=spec.account, record=record, provider_record_json=record.model_dump_json(), normalized=normalized,
                findings=findings, encounters=encounters, comparison_revision=comparison_revision, diagnostic=diagnostic))
            quarantined = quarantined or held
        resumable = page.continuation is not None and page.continuation != job.continuation
        safe = not boundary and (page.complete or resumable)
        complete = page.complete and safe
        status = "complete" if complete else "queued"
        if boundary or (not complete and not resumable) or (complete and quarantined):
            status = "quarantined"
        error = "unresolved_page_boundary" if boundary else (
            "incomplete_window_without_safe_continuation" if not complete and not resumable else (
                "changed_source_requires_D08_resolution" if quarantined else None))
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
