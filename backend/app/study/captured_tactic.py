"""Study policy for admitting a validated tactic on a borrowed writer."""

import json
from collections.abc import Callable
from typing import Protocol, TypedDict

from app.contracts.tactic_capture import TacticCaptureConflict, ValidatedCapture
from app.study.interfaces.captured_tactic import CaptureAdmissionResult


class CaptureCardFacts(TypedDict):
    content_type: str
    archived: bool
    superseded_by: str | None
    pending_validation: bool
    start_fen: str
    moves_json: str


class CaptureAdmissionStorage(Protocol):
    def ensure_capture_repertoire(
        self, repertoire_id: str, name: str, source_name: str, created_at: str
    ) -> None: ...
    def create_capture_card(
        self,
        card_id: str,
        repertoire_id: str,
        starting_fen: str,
        moves_json: str,
        due_date: str,
        source_ref: str | None,
        source_fen: str,
        trained_color: str,
        introduced_at: str,
    ) -> bool: ...
    def capture_card(self, card_id: str) -> CaptureCardFacts | None: ...
    def link_capture_card(self, repertoire_id: str, card_id: str) -> None: ...
    def reopen_capture_card(self, day: str, card_id: str) -> None: ...
    def lock_capture_queue(self, day: str) -> None: ...
    def mark_capture_guided(self, day: str, card_id: str) -> None: ...


class CapturedTacticAdmissionOwner:
    def __init__(
        self,
        storage: CaptureAdmissionStorage,
        lock_daily: Callable[[str], None],
        place: Callable[[str, int, str, str], None],
    ):
        self.storage = storage
        self._lock_daily = lock_daily
        self._place = place

    def lock_daily(self, day: str) -> None:
        self._lock_daily(day)

    def admit(
        self, capture: ValidatedCapture, day: str, now: str
    ) -> CaptureAdmissionResult:
        storage = self.storage
        moves = list(capture.moves)
        tactic_card_id = capture.card_id
        repertoire_id = (
            "__game_tactics__" if capture.game_finding else "__captured_tactics__"
        )
        storage.ensure_capture_repertoire(
            repertoire_id,
            "Game tactics" if capture.game_finding else "Captured tactics",
            "Accepted personal game findings"
            if capture.game_finding
            else "User-captured tactical positions",
            now,
        )
        inserted = storage.create_capture_card(
            tactic_card_id,
            repertoire_id,
            capture.starting_fen,
            json.dumps(moves),
            day,
            capture.source_ref if capture.game_finding else None,
            capture.starting_fen,
            capture.trained_color,
            day,
        )
        saved_card = storage.capture_card(tactic_card_id)
        if (
            saved_card is None
            or saved_card["content_type"] != "tactic"
            or saved_card["archived"]
            or saved_card["superseded_by"]
            or saved_card["pending_validation"]
        ):
            raise TacticCaptureConflict(
                "This position and solution belong to another card type, or an archived, superseded, or unvalidated card. Resolve that card before capturing it."
            )
        if (
            " ".join(saved_card["start_fen"].split()[:4]) != capture.identity_fen
            or json.loads(saved_card["moves_json"]) != moves
        ):
            raise TacticCaptureConflict(
                "The existing card has different content. Repair it before capturing this tactic."
            )
        if inserted:
            storage.link_capture_card(repertoire_id, tactic_card_id)
        storage.reopen_capture_card(day, tactic_card_id)
        storage.lock_capture_queue(day)
        reason = {
            "puzzle_rush": "Captured tactic · Puzzle Rush",
            "game": "Captured tactic · game",
            "manual": "Captured tactic",
            "other": "Captured tactic · other",
        }[capture.source_kind]
        self._place(tactic_card_id, 4, "guided", reason)
        storage.mark_capture_guided(day, tactic_card_id)
        return {
            "card_id": tactic_card_id,
            "reused": not inserted,
            "introduced": inserted,
        }
