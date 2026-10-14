"""Native public package adapter, with no persistence or service initialization."""
from datetime import datetime, timezone
from app.services import scheduler
import tempo_scheduling
from fsrs import Card


def test_scheduling_package_adapter_preserves_public_identity_and_fixed_time_result():
    for public_name in tempo_scheduling.__all__:
        assert getattr(scheduler, public_name) is getattr(tempo_scheduling, public_name)
    reviewed_at = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    initial_card = Card(card_id=1, due=reviewed_at).to_json()
    assert scheduler.schedule_review("correct", fsrs_card_json=initial_card, reviewed_at=reviewed_at) == tempo_scheduling.schedule_review("correct", fsrs_card_json=initial_card, reviewed_at=reviewed_at)
