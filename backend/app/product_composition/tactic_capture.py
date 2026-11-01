"""Bind Tactics capture to the existing Study writer within the supplied transaction."""
from functools import partial
from fastapi import HTTPException
from app.contracts.tactic_capture import TacticCaptureConflict, TacticCaptureValidationError
from app.services import tactic_capture
from app.services.review_service import ensure_card_queued_after
from app.study.captured_tactic import CapturedTacticAdmissionOwner
from tempo_legacy_database.adapters.study import captured_tactic as storage
from tempo_legacy_database.adapters.tactic_admission import lock_daily_tactic_admission


class BorrowedCaptureStorage:
    def __init__(self, database):
        self.database = database

    def ensure_capture_repertoire(self, *args): return storage.ensure_capture_repertoire(self.database, *args)
    def create_capture_card(self, *args): return storage.create_capture_card(self.database, *args)
    def capture_card(self, *args): return storage.capture_card(self.database, *args)
    def link_capture_card(self, *args): return storage.link_capture_card(self.database, *args)
    def reopen_capture_card(self, *args): return storage.reopen_capture_card(self.database, *args)
    def lock_capture_queue(self, *args): return storage.lock_capture_queue(self.database, *args)
    def mark_capture_guided(self, *args): return storage.mark_capture_guided(self.database, *args)


def capture_tactic(database, request, *, game_finding=False):
    admission = CapturedTacticAdmissionOwner(BorrowedCaptureStorage(database),
        partial(lock_daily_tactic_admission, database), partial(ensure_card_queued_after, database))
    try:
        return tactic_capture.capture_tactic(database, request, admission=admission, game_finding=game_finding)
    except TacticCaptureValidationError as error:
        raise HTTPException(422, str(error)) from error
    except TacticCaptureConflict as error:
        raise HTTPException(409, str(error)) from error
