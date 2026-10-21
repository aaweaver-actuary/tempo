"""Frozen Study policy and borrowed-transaction composition after file separation."""
import ast
from pathlib import Path
import sqlite3

import pytest

from tempo_learning_runtime import card_deletion, frozen_opening_order as composed
from tempo_learning_runtime.frozen_maia_math import content_fingerprint
from tempo_learning_runtime.review_conflicts import ReviewConflict
from tempo_learning_runtime.study import frozen_order as policy


def test_frozen_policy_imports_only_its_own_facts_and_standard_library():
    for path in Path(policy.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert (node.level == 1 and node.module in {"interfaces", "policy"}) or (
                    node.level == 0 and node.module in {"typing", "__future__"}
                ), (path.name, ast.unparse(node))
            elif isinstance(node, ast.Import):
                pytest.fail(f"Unexpected frozen-policy implementation import: {ast.unparse(node)}")


def test_inactive_frozen_policy_retains_rows_without_sorting_or_fingerprints():
    def unexpected_fingerprint(*arguments):
        raise AssertionError("No active publication must not inspect content")
    rows = [{"id": None}, {"id": "card"}]
    facts = policy.FrozenAnnotationFacts((None, 4), {}, set(), set())
    assert policy.annotate(iter(rows), facts, fingerprint=unexpected_fingerprint) == [
        {**row, "frozen_token": [None, 4]} for row in rows]
    assert rows == [{"id": None}, {"id": "card"}]


def test_frozen_policy_distinguishes_content_legacy_and_route_protection():
    facts = policy.FrozenAnnotationFacts(
        ("run", 2), {"changed": policy.FrozenRankFacts(1, "old", {"revision": 2}, ("rep",))},
        {"legacy"}, {("fallback", "blocked")},
    )
    rows = policy.annotate(
        [{"id": "changed"}, {"id": "legacy"}, {"id": "fallback", "repertoire_id": "blocked"}],
        facts, fingerprint=lambda *_: "new",
    )
    assert [(r["frozen_rank"], r["frozen_valid"], r["frozen_route_valid"]) for r in rows] == [
        (1, False, True), (None, False, True), (None, True, False)]


@pytest.mark.parametrize("outstanding", [False, True])
def test_frozen_miss_proposal_requires_current_evidence_and_stale_token_calls_nothing(outstanding):
    calls = []
    capabilities = dict(miss_reason="miss",
                        has_outstanding_miss=lambda card: calls.append(card) or outstanding,
                        current_definition_valid=lambda _card: pytest.fail("Miss used ordinary definition path"))
    planned = {"card_id": "card", "reason": "miss", "frozen_token": ["run", 3]}
    assert not policy.proposal_valid(planned, ("run", 4), **capabilities)
    assert calls == []
    assert policy.proposal_valid(planned, ("run", 3), **capabilities) is outstanding
    assert calls == ["card"]


def test_frozen_slot_order_preserves_exposed_unrelated_and_mandatory_miss_slots():
    rows = [dict(id=n, card_id=str(n), position=n, content_type="opening", admission_kind="new",
                 frozen_valid=True, frozen_rank=6-n) for n in range(1, 6)]
    rows[2]["content_type"] = "tactic"
    rows[3]["gameplay_priority_reason"] = "miss"
    assert [row["id"] for row in policy.order_queue_slots(
        rows, miss_reason="miss", card_exposed=lambda card: card == "2",
    )] == [5, 2, 3, 4, 1]


@pytest.fixture
def frozen_database():
    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
          CREATE TABLE frozen_opening_activation(id INTEGER,run_id TEXT,epoch INTEGER);
          CREATE TABLE frozen_opening_ranks(run_id TEXT,card_id TEXT,ordinal INTEGER,fingerprint TEXT);
          CREATE TABLE repertoire_integrity_card_blocks(card_id TEXT,repertoire_id TEXT);
          CREATE TABLE queue_attempt_origins(card_id TEXT,legacy INTEGER);
          CREATE TABLE cards(id TEXT,revision INTEGER,start_fen TEXT,moves_json TEXT,repertoire_id TEXT);
          CREATE TABLE repertoire_cards(card_id TEXT,repertoire_id TEXT);
          INSERT INTO frozen_opening_activation VALUES(1,'run',2);
          INSERT INTO cards VALUES('captured',1,'fen','[]','rep');
          INSERT INTO queue_attempt_origins VALUES('legacy',1);
          INSERT INTO repertoire_integrity_card_blocks VALUES('captured','blocked');
        """)
        card = dict(database.execute("SELECT * FROM cards").fetchone())
        database.execute("INSERT INTO frozen_opening_ranks VALUES('run','captured',1,?)",
                         (content_fingerprint(card, ["rep"]),))
        database.commit()
        yield database


def test_frozen_composition_reads_generator_and_uncommitted_definition_without_committing(frozen_database):
    database = frozen_database
    rows = composed.annotate(database, (row for row in [
        {"card_id": "captured", "repertoire_id": "blocked"}, {"card_id": "legacy"}, {"card_id": "fallback"},
    ]))
    assert [(r["frozen_valid"], r["frozen_route_valid"]) for r in rows] == [
        (True, False), (False, True), (True, True)]
    database.execute("UPDATE cards SET revision=2")
    assert not composed.annotate(database, [{"card_id": "captured"}])[0]["frozen_valid"]
    assert database.in_transaction
    database.rollback()
    assert composed.annotate(database, [{"card_id": "captured"}])[0]["frozen_valid"]


def test_deleted_card_guard_reads_the_callers_uncommitted_fact_and_preserves_conflict():
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE deleted_cards(card_id TEXT)")
        database.execute("INSERT INTO deleted_cards VALUES('card')")
        with pytest.raises(ReviewConflict) as conflict:
            card_deletion.require_card_not_deleted(database, "card")
        assert conflict.value.information() == {
            "code": "card_deleted", "message": "This card was permanently deleted", "retryable": False}
        assert database.in_transaction
        database.rollback()
        assert card_deletion.require_card_not_deleted(database, "card") is None
