"""Independent in-memory binding for the Study next-exercise contract.

Production HTTP/storage conversion is deliberately owned by B13; this proves
only the core and independent fake until that real binding is annotated.
"""
from app.study.next_exercise import HoldFact, Issuance, IssueResult, IssuedPlanFact, QueueReadSnapshot, next_exercise
from app.contracts.queue_issuance import NextExerciseOutcome

Card = dict[str, object]


class FakeOwner:
    def __init__(self) -> None:
        self.cards: list[Card] = [{"queue_entry_id": 1, "id": "card", "revision": 2}]

    def read(self) -> QueueReadSnapshot:
        return QueueReadSnapshot("2026-11-13", "UTC", {"state": "ready"}, [], self.cards, 1)

    def issue(self, day: str, card: Card) -> IssueResult:
        result: Issuance = {"issued": True}
        return None, result

    def current(self) -> tuple[str, list[Card]]:
        return "2026-11-13", self.cards

    def finalize(self, cards: list[Card]) -> None:
        pass

    def manifests(self, cards: list[Card]) -> None:
        pass

    def replay(self, attempt_id: str) -> IssuedPlanFact | HoldFact | None:
        return None

    def is_hold(self, plan: object) -> bool:
        return False

    def issued_manifest(self, plan: IssuedPlanFact, scope: object) -> object:
        return scope

    def shape(self, day: str, cards: list[Card], count: int,
              diagnostics: list[dict[str, object]], projection: dict[str, object] | None) -> dict[str, object]:
        return {"local_date": day, "cards": cards, "count": count,
                "diagnostics": diagnostics, "projection": projection}


def fake_binding(owner: FakeOwner) -> NextExerciseOutcome:
    return next_exercise(read_snapshot=owner.read, issue=owner.issue, current_head=owner.current,
        finalize=owner.finalize, manifests=owner.manifests, replay=owner.replay,
        is_hold=owner.is_hold, issued_manifest=owner.issued_manifest, shape_queue=owner.shape,
        include_opening_evidence=False)
