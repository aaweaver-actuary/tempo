"""One-game feedback preparation and explicit delivery through the Study port.

Preparation is read-only. The caller owns the receiving transaction and must
commit it before reporting admission. This module owns no policy or storage.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path

from tempo_game_review_contracts import CanonicalGame

from app.contracts.product_v02.games import Finding, GameRecord
from app.contracts.product_v02.learning import GameLapse, LearningFeedbackPort, LearningReceipt
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.games.feedback.comparison import compare_game, compare_record, effect_key
from app.repertoire.interfaces import build_index
from app.study.game_feedback import semantic_digest


@dataclass(frozen=True)
class PreparedFeedback:
    """Local composition result, retaining the exact owner command for retry."""
    lapse: GameLapse
    findings: tuple[Finding, ...]
    snapshot_id: str
    snapshot_digest: str

    @property
    def operation_identity(self) -> str:
        return self.lapse.key.logical_id()


def prepare_record_feedback(record: GameRecord, *, provider: str, account: str,
                            workspace_id: str, snapshot: RepertoireSnapshot,
                            detected_at: str, occurrence_ply: int | None = None,
                            game_id: str | None = None) -> PreparedFeedback:
    """Join the public index/comparer and prepare one explicitly mapped lapse.

    The comparer alone decides eligibility. Zero or multiple confirmed matches
    require explicit correction/selection; composition never guesses a card.
    A snapshot and successful preview are not current write authority.
    """
    index = build_index(snapshot)
    findings = compare_record(record, provider=provider, account=account,
        workspace_id=workspace_id, snapshot=snapshot, reentry_lookup=index,
        game_id=game_id)
    return _prepare(findings, workspace_id=workspace_id, snapshot=snapshot,
        detected_at=detected_at, occurred_at=record.played_at,
        source_digest=record.source_digest, occurrence_ply=occurrence_ply)


def prepare_game_feedback(game: CanonicalGame, *, provider: str,
                          time_precision: str, snapshot: RepertoireSnapshot,
                          detected_at: str, occurrence_ply: int | None = None) -> PreparedFeedback:
    """Prepare feedback from retained canonical facts without inventing PGN.

    The caller supplies the existing canonical identity/digest and retained time
    precision. Unknown precision stays unknown in the owning comparison policy.
    """
    index = build_index(snapshot)
    findings = compare_game(game, provider=provider, snapshot=snapshot,
        time_precision=time_precision, reentry_lookup=index)
    return _prepare(findings, workspace_id=game.workspace_id, snapshot=snapshot,
        detected_at=detected_at, occurred_at=game.played_at,
        source_digest=game.source_digest, occurrence_ply=occurrence_ply)


def _prepare(findings, *, workspace_id, snapshot, detected_at, occurred_at,
             source_digest, occurrence_ply):
    selected = tuple(item for item in findings if item.kind == "confirmed_miss"
        and (occurrence_ply is None or item.ply == occurrence_ply))
    if len(selected) != 1:
        raise ValueError("one_confirmed_decision_occurrence_required")
    finding = selected[0]
    lapse = GameLapse(key=effect_key(finding, workspace_id=workspace_id),
        finding=finding, detected_at=detected_at, occurred_at=occurred_at,
        payload_digest=source_digest)
    # Reuse Study's existing pure digest implementation. It ignores the supplied
    # digest and detection/retry clocks; no second semantic hash policy lives here.
    lapse = lapse.model_copy(update={"payload_digest": semantic_digest(lapse)})
    return PreparedFeedback(lapse, findings, snapshot.membership.snapshot_id,
                            snapshot.membership.digest)


def submit_feedback(prepared: PreparedFeedback, feedback: LearningFeedbackPort) -> LearningReceipt:
    """One semantic submission; the injected Study owner validates and mutates.

    Do not acknowledge durable admission until the caller transaction commits.
    On uncertain transport outcome, retain prepared.lapse and read effect_status;
    never construct another logical key or select a fallback writer.
    """
    return feedback.accept_lapse(prepared.lapse)


def _read_json(path: Path):
    with path.open("rb") as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("feedback_input_exceeds_bound")
    document = json.loads(data)
    return document.get("payload", document)


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Prepare one game-feedback command without delivering it")
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--provider", required=True, choices=("lichess", "chesscom"))
    parser.add_argument("--account", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--detected-at", required=True)
    parser.add_argument("--occurrence-ply", type=int)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args(arguments)
    record = GameRecord.model_validate_json(json.dumps(_read_json(options.record)))
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(_read_json(options.snapshot)))
    prepared = prepare_record_feedback(record, provider=options.provider,
        account=options.account, workspace_id=options.workspace_id, snapshot=snapshot,
        detected_at=options.detected_at, occurrence_ply=options.occurrence_ply)
    document = dict(kind="product_feedback_preview", interface_version="0.2.0",
        operation_identity=prepared.operation_identity, snapshot_id=prepared.snapshot_id,
        snapshot_digest=prepared.snapshot_digest, lapse=prepared.lapse.model_dump(mode="json"),
        findings=[item.model_dump(mode="json") for item in prepared.findings])
    # Private game/repertoire evidence stays local; never overwrite a retained
    # preview after an uncertain delivery. The CLI itself has no delivery mode.
    descriptor = os.open(options.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(document, output, indent=2)
    print(json.dumps(dict(prepared=True, delivered=False, read_only=True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
