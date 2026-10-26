"""One bounded import page or owner-command delivery. No infrastructure imports."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

from app.contracts.product_v02.learning import GameEffectControl, GameEffectKey, GameLapse, LearningFeedbackPort
from app.contracts.product_v02.repertoire import RepertoireSnapshotPort

from .ports import (Acquisition, AcquisitionFailure, Capture, Clock, Comparer, Delivery,
                    GamesStore, JobState, Normalizer, PageCommit, SourceState)


def stable_id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def delivery_for(source_key: str, command: GameLapse | GameEffectControl) -> Delivery:
    kind = "lapse" if isinstance(command, GameLapse) else command.action
    revision = command.finding.source_revision if isinstance(command, GameLapse) else command.source_revision
    return Delivery(operation_id=stable_id(kind, command.key.logical_id(), revision), source_key=source_key,
                    lapse=command if isinstance(command, GameLapse) else None,
                    control=command if isinstance(command, GameEffectControl) else None)


class Coordinator:
    def __init__(self, *, providers: Mapping[str, Acquisition], normalizer: Normalizer,
                 comparer: Comparer, snapshots: RepertoireSnapshotPort, store: GamesStore,
                 learning: LearningFeedbackPort, clock: Clock, max_page_records: int = 100,
                 max_page_bytes: int = 8 * 1024 * 1024, max_page_positions: int = 10000):
        if not 1 <= max_page_records <= 100 or not 1 <= max_page_bytes <= 8 * 1024 * 1024:
            raise ValueError("page bounds exceed optional workflow budget")
        if not 1 <= max_page_positions <= 10000:
            raise ValueError("position batch exceeds optional workflow budget")
        self.max_page_positions = max_page_positions
        self.providers, self.normalizer, self.comparer = providers, normalizer, comparer
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
            normalized, findings, diagnostic = None, (), None
            try:
                normalized = self.normalizer.normalize(record, spec)
                if (normalized.workspace_id, normalized.source_revision, normalized.source_digest) != (
                        spec.workspace_id, record.source_revision, record.source_digest):
                    raise ValueError("normalization provenance differs from captured source")
                positions += len(normalized.moves)
                if positions > self.max_page_positions:
                    raise AcquisitionFailure("page_position_budget_exceeded", retryable=False)
                findings = tuple(f for snapshot in snapshots for f in self.comparer.compare(normalized, snapshot))
                if len(findings) > 1000 * len(snapshots):
                    raise AcquisitionFailure("comparison_finding_budget_exceeded", retryable=False)
                for finding in findings:
                    if (finding.provider, finding.game_id, finding.source_revision, finding.source_digest) != (
                            spec.provider, normalized.game_id, record.source_revision, record.source_digest):
                        raise ValueError("comparison provenance differs from captured source")
            except ValueError:
                diagnostic = "unresolved_parsing_or_comparison_boundary"
                boundary = True
            changed = previous is not None and (record.source_digest != previous.original.source_digest
                                                or record.source_revision != previous.original.source_revision)
            held = changed or bool(previous and previous.quarantined) or diagnostic is not None
            effects = list(previous.effects) if previous else []
            confirmed = {(f.decision_id, f.ply) for f in findings if f.kind == "confirmed_miss"}
            for effect in effects:
                if held or (effect.key.decision_id, effect.key.occurrence_ply) not in confirmed:
                    command = GameEffectControl(key=effect.key, source_revision=effect.finding.source_revision,
                        action="hold", reason="source_corrected" if changed else "reanalysis")
                    deliveries.append(delivery_for(key, command))
                    held = True
            if not held:
                prior_plies = {effect.key.occurrence_ply for effect in effects}
                occupied_keys = {(effect.key.decision_id, effect.key.occurrence_ply) for effect in effects}
                for finding in findings:
                    if finding.kind != "confirmed_miss" or (finding.decision_id, finding.ply) in occupied_keys or finding.ply in prior_plies:
                        continue
                    effect_key = GameEffectKey(workspace_id=spec.workspace_id, provider=spec.provider,
                        game_id=finding.game_id, decision_id=finding.decision_id, occurrence_ply=finding.ply)
                    lapse = GameLapse(key=effect_key, finding=finding, detected_at=self.clock.utc_now(),
                        occurred_at=record.played_at, payload_digest=record.source_digest)
                    effects.append(lapse)
                    occupied_keys.add((finding.decision_id, finding.ply))
                    deliveries.append(delivery_for(key, lapse))
            diagnostic = diagnostic or ("changed_source_requires_D08_resolution" if held else None)
            sources.append(SourceState(source_key=key, version=previous.version if previous else 0,
                original=previous.original if previous else record,
                normalized=previous.normalized if changed else normalized, findings=findings,
                comparison_revision=comparison_revision, effects=tuple(effects), quarantined=held, diagnostic=diagnostic))
            captures.append(Capture(source_key=key, account=spec.account, record=record, normalized=normalized,
                findings=findings, comparison_revision=comparison_revision, diagnostic=diagnostic))
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
        self.store.commit_page(PageCommit(job=committed_job, sources=tuple(sources), captures=tuple(captures), deliveries=tuple(deliveries)))

    def deliver(self, delivery: Delivery) -> None:
        """Persist the owner receipt; transport success alone is never evidence."""
        command = delivery.command
        try:
            receipt = (self.learning.control_effect(command) if delivery.control is not None
                       else self.learning.accept_lapse(command))
            revision = command.source_revision if delivery.control else command.finding.source_revision
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
            source_revision=effect.finding.source_revision, action="withdraw",
            reason="source_deleted")) for effect in source.effects)
        quarantined = source.model_copy(update={"quarantined": True,
            "diagnostic": "withdrawn_source_requires_D08_resolution"})
        status = job.model_copy(update={"status": "quarantined", "lease_until": 0,
            "error": "withdrawn_source_requires_D08_resolution"})
        self.store.commit_page(PageCommit(job=status, sources=(quarantined,), captures=(), deliveries=controls))
