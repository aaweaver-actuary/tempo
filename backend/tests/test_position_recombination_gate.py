"""M1: legacy R11 is inert for position ownership, with retained history."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tempo_legacy_database.adapters.study import recombination as owner


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Borrowed:
    read_only = False

    def __init__(self):
        self.raw = SimpleNamespace(
            info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))
        )
        self.modes = {"position": "active", "legacy": "inactive"}
        self.memberships = ["legacy", "position"]
        self.plan = None
        self.review = dict(
            card_id="shared",
            source_kind="study",
            queue_entry_id=7,
            revision=1,
            admission_repertoire_id="legacy",
        )
        self.calls = []
        self.available = True
        self.busy = False

    def execute_native(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        assert not statement.lstrip().startswith(("INSERT", "UPDATE", "DELETE")), (
            "guard must not mutate history"
        )
        if "to_regclass" in statement:
            return Rows([(self.available,)])
        if "study_position_issued_plans" in statement:
            return Rows([self.plan] if self.plan else [])
        if "review_attempt_receipts receipt" in statement:
            return Rows([self.review] if self.review else [])
        if "pg_try_advisory" in statement:
            return Rows([(not self.busy,)])
        if "SELECT o.mode" in statement:
            mode = self.modes.get(parameters[0])
            return Rows(
                [dict(mode=mode, epoch="e2", source_digest="current")] if mode else []
            )
        if "SELECT mode FROM study_position_owners" in statement:
            mode = self.modes.get(parameters[0])
            return Rows([(mode,)] if mode else [])
        if "FROM repertoire_cards" in statement:
            return Rows([(r,) for r in self.memberships])
        if "SELECT repertoire_id FROM repertoire_lines" in statement:
            return Rows([("position",)])
        if "SELECT timezone" in statement:
            return Rows([("UTC",)])
        raise AssertionError(statement)


def test_position_saved_review_returns_explicit_inert_before_legacy_event(monkeypatch):
    db = Borrowed()
    db.review["source_kind"] = "position_study"
    monkeypatch.setattr(owner, "installed", lambda database: True)
    monkeypatch.setattr(
        owner,
        "CurrentStudyRecombination",
        lambda *a, **k: pytest.fail("legacy R11 entered"),
    )
    assert owner.after_saved_review(db, "attempt") == {
        "status": "inert",
        "reason": "position_owned_review",
    }


@pytest.mark.parametrize("mode", ["paused", "inactive", "active"])
def test_original_position_plan_stays_inert_across_pause_and_epoch(mode, monkeypatch):
    db = Borrowed()
    db.modes["position"] = mode
    db.plan = dict(
        card_id="shared", card_revision=1, queue_entry_id=7, repertoire_id="position"
    )
    db.review["admission_repertoire_id"] = "position"
    monkeypatch.setattr(owner, "installed", lambda database: True)
    assert owner.after_saved_review(db, "attempt") == {
        "status": "inert",
        "reason": "position_owned_attempt",
    }
    assert not any("pg_try_advisory" in sql for sql, _ in db.calls)


def test_original_plan_wrong_admission_holds_without_primary_repertoire_inference(
    monkeypatch,
):
    db = Borrowed()
    db.plan = dict(
        card_id="shared", card_revision=1, queue_entry_id=7, repertoire_id="position"
    )
    monkeypatch.setattr(owner, "installed", lambda database: True)
    assert owner.after_saved_review(db, "attempt") == {
        "status": "held",
        "reason": "position_attempt_admission_unproved",
    }


def test_shared_card_requires_admission_and_proven_legacy_scope_remains_legacy():
    db = Borrowed()
    assert owner._position_review_guard(db, "attempt", saved=True) is None
    db.review["admission_repertoire_id"] = None
    assert owner._position_review_guard(db, "attempt", saved=True)["status"] == "held"
    db.review["admission_repertoire_id"] = "position"
    assert owner._position_review_guard(db, "attempt", saved=True)["status"] == "held"
    assert all("cards.repertoire_id" not in sql for sql, _ in db.calls)


def test_busy_owner_holds_and_missing_optional_schema_preserves_legacy():
    db = Borrowed()
    db.busy = True
    assert (
        "position_owner_busy"
        in owner._position_review_guard(db, "attempt", saved=True)["reason"]
    )
    db.available = False
    assert owner._position_review_guard(db, "attempt", saved=True) is None


def test_actual_preparation_skips_position_source_discovery(monkeypatch):
    db = Borrowed()
    monkeypatch.setattr(owner, "installed", lambda database: True)
    monkeypatch.setattr(
        owner,
        "_prepare_recombination_source",
        lambda *a: pytest.fail("source discovery entered"),
    )
    payload = {"card_id": "shared", "review": {"attempt_id": "pending"}}
    result = owner.prepare_recombination_review(
        payload, read_connection=lambda: nullcontext(db)
    )
    assert result["prepared_recombination"].reason.startswith(
        "position_recombination_disabled"
    )
    assert result["review"] is payload["review"]


def test_non_postgres_preparation_preserves_optional_schema_fallback():
    db = Borrowed()
    result = owner.prepare_recombination_review(
        {"card_id": "shared", "review": {}}, read_connection=lambda: nullcontext(db)
    )
    assert result["prepared_recombination"] == owner.PreparedRecombination()
    assert db.calls == []


def test_actual_route_consumer_holds_before_legacy_source_or_generated_card_reads():
    db = Borrowed()
    boundary = owner.CurrentStudyRecombination(db)
    assert boundary.try_current("route") is None
    assert boundary.reason.startswith("position_recombination_disabled")
    assert not any("FROM cards" in sql or "UPDATE" in sql for sql, _ in db.calls)


# Reuse the existing actual retained-owner fixture. It creates a uniquely named
# task DB on an explicitly supplied disposable server and drops only that DB.
from test_product_recombination_postgres import current_owner_transaction


def test_native_legacy_three_clean_join_then_position_guard_preserves_generated_history(
    current_owner_transaction, monkeypatch
):
    from test_product_recombination_postgres import ordinary_current_review

    transaction = current_owner_transaction
    for card in ("A", "C", "B"):
        for _ in range(3):
            with transaction() as database:
                result, payload = ordinary_current_review(database, card, monkeypatch)
    assert result["recombination"]["status"] == "applied"
    generated = result["recombination"]["resulting_presentation_id"]
    attempt = payload["review"]["attempt_id"]
    with transaction() as database:
        assert (
            database.execute_native(
                "SELECT count(*) FROM study_recombination_changes"
            ).fetchone()[0]
            == 1
        )
        database.execute_native(
            "INSERT INTO study_position_epochs VALUES ('r11-repertoire','m1-epoch','source',now())"
        )
        database.execute_native(
            "INSERT INTO study_position_owners VALUES ('r11-repertoire','m1-epoch','active',now())"
        )
        # Exercise I1's actual saved source kind with the retained canonical
        # receipt/origin. This fixture does not claim a position FSRS save.
        database.execute_native(
            "UPDATE reviews SET source_kind='position_study' WHERE id=(SELECT review_id FROM review_attempt_receipts WHERE attempt_id=%s)",
            (attempt,),
        )
        tables = (
            "cards",
            "reviews",
            "queue_attempt_origins",
            "review_attempt_receipts",
            "opening_evidence_attempts",
            "study_recombination_events",
            "study_recombination_streaks",
            "study_recombination_changes",
            "study_recombination_presentations",
            "study_recombination_current",
        )

        def snapshot():
            return {
                table: [
                    row[0]
                    for row in database.execute_native(
                        "SELECT to_jsonb(t) FROM "
                        + table
                        + " t ORDER BY to_jsonb(t)::text"
                    ).fetchall()
                ]
                for table in tables
            }

        before = snapshot()
        assert owner.after_saved_review(database, attempt) == {
            "status": "inert",
            "reason": "position_owned_review",
        }
        assert owner._prepare_recombination_source(
            database, generated
        ).reason.startswith("position_recombination_disabled")
        boundary = owner.CurrentStudyRecombination(database)
        assert boundary.try_current("r11-route") is None
        with pytest.raises(ValueError, match="position_recombination_disabled"):
            owner.accept_current_split(
                database, generated, 1, confirmed_queue_entry_id=999
            )
        assert snapshot() == before
        assert (
            database.execute_native(
                "SELECT id FROM cards WHERE id=%s", (generated,)
            ).fetchone()[0]
            == generated
        )
