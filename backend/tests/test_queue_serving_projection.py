"""The serving query exposes a finite current card contract, not future columns."""

import re
import sqlite3
from pathlib import Path

import pytest
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.http_composition import queue_projection
from app.study.queue_serving import add_encounter_evidence

CARD_FIELDS = """id repertoire_id kind start_fen moves_json state due_date interval_days ease
repetitions lapses unlock_after_card_id fsrs_card_json first_correct_at reinforcement_pending
stability guided_review maximum_interval content_type scheduling_mode hard_correct_streak
recent_attempts_json archived superseded_by source_ref source_fen revision introduced_at
trained_color pending_validation prefix_split_rejected_after_review_id study_exercise_id
canonical_route_source""".split()


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("limit", [None, 20])
def test_queue_projection_emits_only_explicit_current_card_fields(
    monkeypatch, configured, limit
):
    class Capture:
        def execute(self, sql, params):
            self.sql, self.params = sql, params
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(postgres_store, "configured", lambda: configured)
    database = Capture()
    assert (
        queue_projection.queue_rows(database, "2026-10-30", limit, miss_reason="miss")
        == []
    )
    selected = database.sql.split(")) admission_source,", 1)[1].split(
        "COALESCE(r.name", 1
    )[0]
    assert re.findall(r"c\.([a-z_]+)", selected) == CARD_FIELDS
    assert "c.*" not in selected
    assert database.params == (
        (("2026-10-30", limit, limit) if configured else ("2026-10-30", limit))
        if limit is not None
        else ("2026-10-30",)
    )


def test_projected_card_fields_exist_in_current_migrated_schema():
    root = Path(__file__).resolve().parents[2]
    initial = (
        root / "database/tempo_legacy_database/ddl/migrations/001_initial.sql"
    ).read_text()
    create = re.search(r'CREATE TABLE "cards" \(.*?\);', initial).group()
    conn = sqlite3.connect(":memory:")
    conn.execute(create)
    conn.execute(
        "ALTER TABLE cards ADD COLUMN canonical_route_source BIGINT NOT NULL DEFAULT 1"
    )
    actual = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    assert set(CARD_FIELDS) <= actual


def test_encounter_unavailable_is_not_zero_and_available_badges_survive():
    card = {"start_fen": "a b c d 0 1"}
    add_encounter_evidence(card, None, "2026-10-25")
    assert card == {
        "start_fen": "a b c d 0 1",
        "encounter_evidence_status": "unavailable",
        "last_encountered_at": None,
        "encounter_badges": [],
    }
    add_encounter_evidence(
        card,
        {"a b c d": {"encounter_count": 3, "last_seen_at": "2026-10-30"}},
        "2026-10-25",
    )
    assert card["encounter_count_30d"] == 3
    assert card["encounter_badges"] == ["Seen recently", "Frequent"]
