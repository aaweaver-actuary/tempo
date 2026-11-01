"""Bind Tactics capture to the existing Study writer within the supplied transaction."""
from functools import partial
from fastapi import HTTPException
from app.contracts.tactic_capture import TacticCaptureConflict, TacticCaptureValidationError
from app.contracts.transport.tactics import TacticCaptureRequest
from app.services import tactic_capture
from app.services.review_service import ensure_card_queued_after
from app.study.captured_tactic import CaptureCardFacts, CapturedTacticAdmissionOwner
from tempo_legacy_database.adapters.study import captured_tactic as storage
from tempo_legacy_database.adapters.tactic_admission import lock_daily_tactic_admission


class BorrowedCaptureStorage:
    def __init__(self, database):
        self.database = database

    def ensure_capture_repertoire(self, repertoire_id: str, name: str, source_name: str, created_at: str) -> None:
        storage.ensure_capture_repertoire(self.database, repertoire_id, name, source_name, created_at)

    def create_capture_card(self, card_id: str, repertoire_id: str, starting_fen: str, moves_json: str,
                            due_date: str, source_ref: str | None, source_fen: str,
                            trained_color: str, introduced_at: str) -> bool:
        return storage.create_capture_card(self.database, card_id, repertoire_id, starting_fen, moves_json,
                                           due_date, source_ref, source_fen, trained_color, introduced_at)

    def capture_card(self, card_id: str) -> CaptureCardFacts | None:
        row = storage.capture_card(self.database, card_id)
        if row is None:
            return None
        return {"content_type": row["content_type"], "archived": bool(row["archived"]),
                "superseded_by": row["superseded_by"], "pending_validation": bool(row["pending_validation"]),
                "start_fen": row["start_fen"], "moves_json": row["moves_json"]}

    def link_capture_card(self, repertoire_id: str, card_id: str) -> None:
        storage.link_capture_card(self.database, repertoire_id, card_id)

    def reopen_capture_card(self, day: str, card_id: str) -> None:
        storage.reopen_capture_card(self.database, day, card_id)

    def lock_capture_queue(self, day: str) -> None:
        storage.lock_capture_queue(self.database, day)

    def mark_capture_guided(self, day: str, card_id: str) -> None:
        storage.mark_capture_guided(self.database, day, card_id)


def capture_tactic(database, request: TacticCaptureRequest, *, game_finding: bool = False) -> dict[str, object]:
    admission = CapturedTacticAdmissionOwner(BorrowedCaptureStorage(database),
        partial(lock_daily_tactic_admission, database), partial(ensure_card_queued_after, database))
    try:
        return tactic_capture.capture_tactic(database, request, admission=admission, game_finding=game_finding)
    except TacticCaptureValidationError as error:
        raise HTTPException(422, str(error)) from error
    except TacticCaptureConflict as error:
        raise HTTPException(409, str(error)) from error
