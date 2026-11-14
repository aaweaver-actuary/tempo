"""Borrowed, transaction-bound Study admission capability for validated captures."""

from typing import Protocol, TypedDict

from app.contracts.tactic_capture import ValidatedCapture


class CaptureAdmissionResult(TypedDict):
    card_id: str
    reused: bool
    introduced: bool


class CapturedTacticAdmission(Protocol):
    def lock_daily(self, day: str) -> None: ...
    def admit(
        self, capture: ValidatedCapture, day: str, now: str
    ) -> CaptureAdmissionResult: ...
