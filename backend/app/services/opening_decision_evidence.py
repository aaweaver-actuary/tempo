"""Pure manifests and ordered observation semantics, independent of scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import chess

from ..opening_evidence_contracts import OpeningDecisionManifest, OpeningEvidenceCheckpoint
from .opening_segmentation import POLICY_VERSION, presentation_occurrences, stable_key


class EvidenceConflict(ValueError):
    pass


def evidence_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Evidence timestamps require a timezone and cannot be in the future")
    return moment


def decision_manifest(snapshot: dict, repertoire_id: str) -> dict:
    board = chess.Board(snapshot["start_fen"])
    if not board.is_valid():
        raise ValueError("Opening presentation starts from an invalid position")
    presentation = {**snapshot, "id": snapshot["card_id"]}
    occurrences = presentation_occurrences(repertoire_id, presentation)
    identity = stable_key("opening-decision-manifest", 1, POLICY_VERSION, repertoire_id,
                          snapshot["card_id"], int(snapshot["revision"]),
                          snapshot["trained_color"], snapshot["start_fen"],
                          snapshot["moves_json"])
    return OpeningDecisionManifest(
        manifest_id=identity, presentation_snapshot_id=snapshot["id"],
        repertoire_id=repertoire_id, card_id=snapshot["card_id"],
        card_revision=snapshot["revision"], trained_color=snapshot["trained_color"],
        decisions=[{key: occurrence[source] for key, source in (
            ("decision_id", "decision_id"), ("decision_index", "decision_index"),
            ("move_offset", "move_offset"), ("fen", "decision_fen"),
            ("expected_uci", "expected_uci"))} for occurrence in occurrences],
    ).model_dump(mode="json")


def validate_checkpoint(request: OpeningEvidenceCheckpoint, authoritative_manifest: dict) -> None:
    if request.manifest.model_dump(mode="json") != authoritative_manifest:
        raise EvidenceConflict("The opening manifest differs from its immutable saved presentation")
    if request.parent_attempt_id == request.attempt_id:
        raise EvidenceConflict("An attempt cannot name itself as its parent")
    ZoneInfo(request.study_timezone)
    evidence_time(request.started_at)
    if request.terminal:
        evidence_time(request.terminal.ended_at)
    identities: dict[int, dict] = {}
    for event in request.events:
        if event.decision_index >= len(request.manifest.decisions):
            raise EvidenceConflict("Decision index is outside the opening manifest")
        decision = request.manifest.decisions[event.decision_index]
        if event.decision_id != decision.decision_id or event.expected_uci != decision.expected_uci:
            raise EvidenceConflict("Decision identity or expected move differs from the saved manifest")
        evidence_time(event.observed_at)
        payload = event.model_dump(mode="json")
        if event.sequence in identities and identities[event.sequence] != payload:
            raise EvidenceConflict("An event sequence was reused for different content")
        identities[event.sequence] = payload
        if request.terminal and event.sequence > request.terminal.final_sequence:
            raise EvidenceConflict("An event is beyond the attempt's final sequence")


def reduce_observations(events: list[dict], study_timezone: str) -> list[dict]:
    """Reduce a contiguous prefix only; delivery order cannot change recall facts."""
    observations: dict[int, dict] = {}
    previous_index = -1
    for expected_sequence, event in enumerate(events, 1):
        if event["sequence"] != expected_sequence:
            raise EvidenceConflict("Observation reduction requires a contiguous event prefix")
        decision_index = event["decision_index"]
        if decision_index < previous_index:
            raise EvidenceConflict("A later event cannot return to an earlier learner decision")
        previous_index = decision_index
        observation = observations.setdefault(decision_index, {
            "decision_index": decision_index, "decision_id": event["decision_id"],
            "expected_uci": event["expected_uci"], "first_response_uci": None,
            "first_response_correct": None, "assistance_before_response": [],
            "assistance": [], "manual_failure": False, "revealed": False,
            "corrected": False, "observed_at": event["observed_at"],
            "response_at": None, "failure_at": None, "disposition": None,
        })
        kind = event["kind"]
        answered = observation["first_response_uci"] is not None or observation["manual_failure"]
        if kind == "assistance":
            assistance = event["assistance"]
            if assistance not in observation["assistance"]:
                observation["assistance"].append(assistance)
            if not answered and assistance not in observation["assistance_before_response"]:
                observation["assistance_before_response"].append(assistance)
        elif kind == "first_response":
            if answered:
                raise EvidenceConflict("A decision already has its first response or manual failure")
            observation.update(first_response_uci=event["response_uci"],
                               first_response_correct=event["response_uci"] == event["expected_uci"],
                               response_at=event["observed_at"], disposition=event.get("disposition"))
            if not observation["first_response_correct"]:
                observation["failure_at"] = event["observed_at"]
        elif kind == "manual_failure":
            if answered:
                raise EvidenceConflict("Manual failure cannot replace an existing first response")
            observation.update(manual_failure=True, failure_at=event["observed_at"],
                               response_at=event["observed_at"])
        elif kind == "reveal":
            if observation["revealed"]:
                raise EvidenceConflict("A decision was revealed twice")
            observation["revealed"] = True
            if "revealed" not in observation["assistance"]:
                observation["assistance"].append("revealed")
            if not answered and "revealed" not in observation["assistance_before_response"]:
                observation["assistance_before_response"].append("revealed")
        elif kind == "correction":
            if (not answered or observation["first_response_correct"] is True or
                    observation["corrected"] or event["response_uci"] != event["expected_uci"]):
                raise EvidenceConflict("A correction must follow a failed decision and match its expected move")
            observation["corrected"] = True
    for observation in observations.values():
        observation["clean"] = observation["first_response_correct"] is True and not observation["assistance_before_response"] and observation["disposition"] not in {"illegal", "unverified"}
        study_time = evidence_time(observation["response_at"] or observation["observed_at"])
        observation["study_day"] = study_time.astimezone(ZoneInfo(study_timezone)).date().isoformat()
    return list(observations.values())
