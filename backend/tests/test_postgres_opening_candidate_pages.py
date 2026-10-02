"""Opening admissions must read bounded candidate pages before planning."""

from app import main
from app.services import postgres_queue_refresh


def test_postgres_opening_candidates_read_small_pages_before_planning(monkeypatch):
    selected_pages = []
    identifiers = iter([[("a",), ("b",)], [("c",)], []])

    def read_page(statement, parameters=(), *, native=False):
        if statement.startswith("SELECT DISTINCT event.card_id"):
            return []
        if statement.startswith("SELECT id FROM cards"):
            assert native
            assert parameters[1] == postgres_queue_refresh._OPENING_CANDIDATE_READ_BATCH_SIZE
            return next(identifiers)
        if statement.startswith("WITH active_miss"):
            assert native
            assert "eligible_cards AS MATERIALIZED" in statement
            selected_pages.append(parameters[1])
            return [{"id": card_id, "repertoire_id": "rep",
                     "gameplay_priority_reason": None} for card_id in parameters[1]]
        if statement.startswith("SELECT COALESCE(q.admission_repertoire_id"):
            return []
        if statement.startswith("SELECT r.id,COALESCE"):
            return [("rep", 2)]
        raise AssertionError(statement[:100])

    monkeypatch.setattr(postgres_queue_refresh, "_bounded_read", read_page)
    monkeypatch.setattr(
        main, "_plan_prioritized_opening_admissions",
        lambda candidates, _counts, _day, _limit: [
            ("rep", candidate) for candidate in candidates
        ],
    )
    plan = postgres_queue_refresh._prepare_prioritized_openings("2026-09-28")
    assert selected_pages == [["a", "b"], ["c"]]
    assert [item["card_id"] for item in plan] == ["a", "b", "c"]
