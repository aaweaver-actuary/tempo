"""An admitted encounter stays priority under ordinary queue presentation."""
from app.services.queue_ordering import _priority_queue_order


def test_encounter_priority_precedes_movable_work_without_moving_issued_head():
    rows = [dict(id=number, card_id=f"card-{number}", position=number - 1,
                 content_type="opening", admission_kind="review", issued=number == 1,
                 gameplay_priority_reason="product_game_encounter" if number == 3 else None)
            for number in (1, 2, 3)]
    ordered = _priority_queue_order(rows)
    assert [row["id"] for row in ordered] == [1, 3, 2]
    assert ordered[0] is rows[0] and ordered[1] is rows[2]
