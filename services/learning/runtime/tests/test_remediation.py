"""The actual Learning intake, issuance, single writer and review transaction."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event
import json, uuid

import chess, pytest
from fastapi import HTTPException
from tempo_chess_core import canonical_fen_key
from tempo_learning_runtime import receivers, review_adapter
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.remediation_adapter import BorrowedConnection, repository
from tempo_learning_runtime.maintenance import run_once, finish_slice, SupersededWork
from tempo_learning_runtime.durable_tasks import claim
from test_runtime import activate, bundle, head, review_payload, submit
from test_evidence_revisions import send, drain, domain_state
from concurrency_helpers import extend_gate_budget, wait_for_lock


def evidence(
    definition,
    *,
    revision=1,
    transport=None,
    move="g1f3",
    evidence_id="evidence-a",
    game_id="game-a",
    occurred_at=None,
):
    return {
        "contract_version": 2,
        "workspace_id": "workspace-a",
        "delivery_id": transport or f"delivery-{revision}",
        "evidence_id": evidence_id,
        "evidence_revision": revision,
        "game_id": game_id,
        "game_revision": revision,
        "occurred_at": occurred_at,
        "encounters": [
            {
                "encounter_id": "encounter-a",
                "ply": 0,
                "position_key": canonical_fen_key(definition["starting_fen"]),
                "decision_id": definition["decision_id"],
                "observed_move": move,
            }
        ],
        "comparisons": [
            {
                "encounter_id": "encounter-a",
                "card_id": definition["card_id"],
                "decision_id": definition["decision_id"],
                "publication_id": definition["publication_id"],
                "publication_generation": definition["publication_generation"],
                "expected_moves": sorted(
                    {line[0] for line in definition["specification"]["accepted_lines"]}
                ),
                "status": "missed",
            }
        ],
        "misses": [
            {
                "miss_id": "miss-observation",
                "encounter_id": "encounter-a",
                "decision_id": definition["decision_id"],
                "reason": "repertoire_deviation",
            }
        ],
        "obligations": [
            {
                "obligation_id": "obligation-a",
                "obligation_revision": revision,
                "source_evidence_id": evidence_id,
                "source_revision": revision,
                "card_id": definition["card_id"],
                "card_revision": definition["exercise_revision"],
                "decision_id": definition["decision_id"],
                "publication_id": definition["publication_id"],
                "publication_generation": definition["publication_generation"],
                "encounter_id": "encounter-a",
                "miss_id": "miss-observation",
                "kind": "real_game_miss",
            }
        ],
        "withdrawals": [],
    }


def accepted(case, payload):
    response = send(case, payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", response.text
    return response.json()


def test_v2_acceptance_uses_borrowed_rows_and_never_writes_legacy_obligations(case):
    definition = activate(case)["publication"]["definitions"][0]
    payload = evidence(definition)
    receipt = accepted(case, payload)
    assert send(case, payload).json() == receipt
    assert accepted(case, {**payload, "delivery_id": "semantic-retry"}) == {
        **receipt,
        "delivery_id": "semantic-retry",
    }
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 1
    )
    assert case.query("SELECT COUNT(*) FROM learning.learning_obligations")[0][0] == 0
    assert case.query("SELECT COUNT(*) FROM learning.evidence_deliveries")[0][0] == 0
    with case.database.scope(), connection() as database:
        factory = database.raw.row_factory
        assert repository(database).has_outstanding(definition["card_id"])
        assert database.raw.row_factory is factory
        assert (
            database.execute("SELECT 1 AS retained_row").fetchone()["retained_row"] == 1
        )


def test_successful_review_settles_bound_frontier_atomically_and_replays(case):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    drain(case)
    card = head(case)
    bound = tuple(
        case.query(
            "SELECT issued_at,frontier_sequence FROM learning.remediation_attempts"
        )[0]
    )
    assert bound[1] == 1
    assert head(case)["queue_entry_id"] == card["queue_entry_id"]
    assert (
        tuple(
            case.query(
                "SELECT issued_at,frontier_sequence FROM learning.remediation_attempts"
            )[0]
        )
        == bound
    )
    payload = review_payload(card)
    operation = uuid.uuid4().hex
    saved = submit(case, card, payload, operation)
    assert saved.status_code == 200, saved.text
    assert submit(case, card, payload, operation).json() == saved.json()
    assert submit(case, card, payload).json() == saved.json()
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_settlements")[0][0] == 1
    )
    assert (
        case.query("SELECT settled FROM learning.remediation_review_receipts")[0][0]
        is True
    )
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.daily_queue WHERE gameplay_priority_reason IS NOT NULL"
        )[0][0]
        == 0
    )


@pytest.mark.parametrize(
    "known_time", [False, True], ids=["unknown-time", "late-historical-time"]
)
def test_miss_after_issuance_requires_a_fresh_attempt(case, known_time):
    definition = activate(case)["publication"]["definitions"][0]
    original = head(case)
    payload = evidence(
        definition,
        occurred_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        if known_time
        else None,
    )
    accepted(case, payload)
    drain(case)
    assert head(case)["queue_entry_id"] == original["queue_entry_id"]
    assert submit(case, original, review_payload(original)).status_code == 200
    assert (
        case.query("SELECT settled FROM learning.remediation_review_receipts")[0][0]
        is False
    )
    drain(case)
    fresh = head(case)
    assert fresh["queue_entry_id"] != original["queue_entry_id"]
    assert submit(case, fresh, review_payload(fresh)).status_code == 200
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        == 1
    )


def test_v2_meaningful_correction_and_targeted_withdrawal_are_replay_safe(case):
    definition = activate(case)["publication"]["definitions"][0]
    first = evidence(definition)
    initial = accepted(case, first)
    corrected = evidence(definition, revision=2, move="c2c4")
    accepted(case, corrected)
    assert case.query("SELECT sequence FROM learning.remediation_cards")[0][0] == 2
    stale = evidence(definition, revision=1, transport="stale")
    stale["obligations"][0]["obligation_id"] = "stale-new-id"
    assert send(case, stale).json()["status"] == "conflict"
    # A never-accepted lower revision is stale; a changed accepted revision is
    # an immutable-identity conflict. Neither creates effects.
    skipped = evidence(definition, revision=5, transport="revision-five", move="c2c4")
    accepted(case, skipped)
    unseen = evidence(definition, revision=4, transport="unseen-four")
    assert send(case, unseen).json()["status"] == "stale_revision"
    withdrawal = {
        **evidence(definition, revision=6),
        "encounters": [],
        "comparisons": [],
        "misses": [],
        "obligations": [],
        "withdrawals": [
            {
                "obligation_id": "obligation-a",
                "expected_obligation_revision": 5,
                "reason": "source_corrected",
            }
        ],
    }
    accepted(case, withdrawal)
    assert send(case, first).json() == initial
    assert (
        case.query("SELECT active FROM learning.remediation_obligation_heads")[0][0]
        is False
    )
    drain(case)
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.daily_queue WHERE gameplay_priority_reason IS NOT NULL"
        )[0][0]
        == 0
    )


@pytest.mark.parametrize(
    "change", ["archived", "deleted", "invalid", "buried", "retired"]
)
def test_remediation_never_bypasses_card_lifecycle_or_validity(case, change):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    identifier = definition["card_id"]
    if change == "deleted":
        case.query(
            "INSERT INTO learning.deleted_cards(card_id) VALUES(%s)", (identifier,)
        )
    elif change == "buried":
        case.query(
            "UPDATE learning.daily_queue SET status='buried' WHERE card_id=%s",
            (identifier,),
        )
    else:
        field = {
            "archived": "archived=1",
            "invalid": "pending_validation=1",
            "retired": "serving_active=0",
        }[change]
        case.query("UPDATE learning.cards SET " + field + " WHERE id=%s", (identifier,))
    before = domain_state(case)
    drain(case)
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.daily_queue WHERE gameplay_priority_reason IS NOT NULL"
        )[0][0]
        == 0
    )
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 1
    )


@pytest.mark.parametrize("guided,failed", [(True, False), (False, True)])
def test_guided_or_failed_review_does_not_settle_remediation(case, guided, failed):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    drain(case)
    card = head(case)
    if failed:
        result = case.client.post(
            f"/api/queue/entries/{card['queue_entry_id']}/fail",
            json={"card_id": card["id"], "expected_revision": card["revision"]},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        assert result.status_code == 200, result.text
    result = submit(case, card, review_payload(card, guided=guided))
    assert result.status_code == 200, result.text
    assert (
        case.query("SELECT settled FROM learning.remediation_review_receipts")[0][0]
        is False
    )


@pytest.mark.parametrize("handled", [False, True])
def test_required_settlement_failure_rolls_back_review_schedule_queue_and_receipts(
    case, monkeypatch, handled
):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    drain(case)
    card = head(case)
    before = domain_state(case)
    from tempo_learning_runtime.review_conflicts import ReviewConflict

    original = BorrowedConnection.execute

    def fail_after_settlement(self, statement, parameters=()):
        cursor = original(self, statement, parameters)
        if statement.startswith("INSERT INTO remediation_review_receipts"):
            raise ReviewConflict(
                "injected_settlement_failure", "Required settlement failed"
            )
        return cursor

    monkeypatch.setattr(BorrowedConnection, "execute", fail_after_settlement)
    result = submit(
        case, card, review_payload(card), suffix="/reconcile" if handled else ""
    )
    assert result.status_code == (200 if handled else 409), result.text
    if handled:
        assert result.json()["persisted"] is False
    assert domain_state(case) == before
    for table in [
        "reviews",
        "review_attempt_receipts",
        "remediation_settlements",
        "remediation_review_receipts",
    ]:
        assert case.query("SELECT COUNT(*) FROM learning." + table)[0][0] == 0, table


def test_superseded_maintenance_slice_rolls_back_admission_and_cursor(case):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    with case.database.scope():
        with connection() as database:
            task = claim(database)
        before = domain_state(case)
        with pytest.raises(SupersededWork):
            with connection() as database:
                from tempo_learning_runtime.remediation_adapter import maintain_one

                payload = json.loads(task["payload_json"])
                maintain_one(database, payload)
                finish_slice(
                    database,
                    {**task, "generation": task["generation"] + 1},
                    payload,
                    False,
                )
    assert domain_state(case) == before


def opening_definitions(moves):
    return bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [[move]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
            for move in moves
        ]
    )


def test_zero_allowance_miss_introduces_locked_card_without_admitting_ordinary_sibling(
    case,
):
    payload = opening_definitions(["e2e4", "d2d4"])
    payload["repertoire_limits"] = {"repertoire-a": 0}
    payload["facts"][1]["parent_card_id"] = payload["facts"][0]["card_id"]
    activate(case, payload)
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 0
    definition = payload["publication"]["definitions"][1]
    accepted(case, evidence(definition))
    drain(case)
    card = head(case)
    assert card["id"] == definition["card_id"] and card["admission_kind"] == "new"
    assert card["gameplay_priority_reason"]
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 1


def test_miss_promotion_preserves_protected_positions_ordinary_order_and_frozen_inputs(
    case,
):
    from tempo_learning_runtime.frozen_maia_math import content_fingerprint

    payload = opening_definitions(["e2e4", "d2d4", "c2c4", "g1f3", "b1c3"])
    ranks = []
    for index, (definition, fact) in enumerate(
        zip(payload["publication"]["definitions"], payload["facts"])
    ):
        card = {
            "id": definition["card_id"],
            "revision": 1,
            "start_fen": definition["starting_fen"],
            "moves_json": json.dumps(definition["specification"]["accepted_lines"][0]),
            "trained_color": "white",
            "kind": "checkpoint",
            "content_type": "opening",
            "source_ref": None,
            "source_fen": None,
        }
        ranks.append(
            {
                "card_id": card["id"],
                "ordinal": 5 - index,
                "fingerprint": content_fingerprint(card, fact["memberships"]),
            }
        )
    payload["frozen_order"] = {
        "reader_version": "frozen-maia-v2",
        "run_id": "f1-frozen-fixture",
        "ranks": ranks,
    }
    activate(case, payload)
    issued = head(case)
    rows = [
        tuple(row)
        for row in case.query(
            "SELECT id,card_id,position FROM learning.daily_queue ORDER BY position,id"
        )
    ]
    explicit = rows[2]
    case.query(
        "UPDATE learning.daily_queue SET admission_kind='explicit' WHERE id=%s",
        (explicit[0],),
    )
    target = rows[-1]
    definition = next(
        value
        for value in payload["publication"]["definitions"]
        if value["card_id"] == target[1]
    )
    before_ranks = [
        tuple(row) for row in case.query("SELECT * FROM learning.frozen_opening_ranks")
    ]
    accepted(case, evidence(definition, move="g2g3"))
    drain(case)
    after = [
        tuple(row)
        for row in case.query(
            "SELECT id,card_id,position FROM learning.daily_queue ORDER BY position,id"
        )
    ]
    assert (
        next(row[2] for row in after if row[0] == issued["queue_entry_id"])
        == rows[0][2]
    )
    assert next(row[2] for row in after if row[0] == explicit[0]) == explicit[2]
    assert [
        row[0]
        for row in after
        if row[0] not in {target[0], issued["queue_entry_id"], explicit[0]}
    ] == [
        row[0]
        for row in rows
        if row[0] not in {target[0], issued["queue_entry_id"], explicit[0]}
    ]
    assert before_ranks == [
        tuple(row) for row in case.query("SELECT * FROM learning.frozen_opening_ranks")
    ]


def test_concurrent_deliveries_for_same_obligation_have_one_logical_effect(case):
    definition = activate(case)["publication"]["definitions"][0]
    payload = evidence(definition)

    def deliver(transport):
        with case.database.scope(), connection() as database:
            extend_gate_budget(database)
            return receivers.receive(
                database, {**payload, "delivery_id": transport}, "workspace-a"
            )

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(deliver, ["concurrent-a", "concurrent-b"]))
    assert all(result["status"] == "completed" for result in results)
    drain(case)
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 1
    )
    assert case.query("SELECT sequence FROM learning.remediation_cards")[0][0] == 1
    assert (
        case.query("SELECT COUNT(*) FROM learning.daily_queue WHERE status='queued'")[
            0
        ][0]
        == 1
    )


@pytest.mark.parametrize("first_operation", ["review", "evidence"])
def test_review_and_evidence_races_preserve_issued_frontier_in_both_commit_orders(
    case, monkeypatch, first_operation
):
    definition = activate(case)["publication"]["definitions"][0]
    original_head = head(case)
    payload = evidence(definition)
    review = review_payload(original_head)
    entered, release, contender_started = Event(), Event(), Event()
    contender_pid = []
    execute = BorrowedConnection.execute
    schedule = review_adapter.LearningReviewAdapter.schedule

    def gate_acceptance(self, statement, parameters=()):
        result = execute(self, statement, parameters)
        if first_operation == "evidence" and statement.startswith(
            "INSERT INTO remediation_contributions"
        ):
            entered.set()
            assert release.wait(3)
        return result

    def gate_review(self, *arguments, **keywords):
        result = schedule(self, *arguments, **keywords)
        if first_operation == "review":
            entered.set()
            assert release.wait(3)
        return result

    monkeypatch.setattr(BorrowedConnection, "execute", gate_acceptance)
    monkeypatch.setattr(review_adapter.LearningReviewAdapter, "schedule", gate_review)

    def apply(operation, contender=False):
        with case.database.scope(), connection() as database:
            extend_gate_budget(database)
            if contender:
                contender_pid.append(
                    database.execute_native("SELECT pg_backend_pid()").fetchone()[0]
                )
                contender_started.set()
            if operation == "evidence":
                return receivers.receive(database, payload, "workspace-a")
            return review_adapter.submit_legacy_review(
                database, {"card_id": original_head["id"], "review": review}
            )

    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(apply, first_operation)
        try:
            assert entered.wait(3)
            second = executor.submit(
                apply, "evidence" if first_operation == "review" else "review", True
            )
            assert contender_started.wait(3)
            wait_for_lock(case, contender_pid[0])
        finally:
            release.set()
        results = [first.result(), second.result()]
    assert any(result.get("persisted") is True for result in results)
    assert any(result.get("status") == "completed" for result in results)
    assert (
        case.query("SELECT settled FROM learning.remediation_review_receipts")[0][0]
        is False
    )
    drain(case)
    fresh = head(case)
    assert fresh["queue_entry_id"] != original_head["queue_entry_id"]
    assert submit(case, fresh, review_payload(fresh)).status_code == 200
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        == 1
    )


def test_acceptance_receipt_and_durable_work_request_rollback_together(
    case, monkeypatch
):
    from tempo_learning_runtime import remediation_adapter

    definition = activate(case)["publication"]["definitions"][0]
    before = domain_state(case)
    original = remediation_adapter.request_remediation

    def fail_after_enqueue(database, *arguments):
        original(database, *arguments)
        raise HTTPException(409, "Injected after durable remediation enqueue")

    monkeypatch.setattr(remediation_adapter, "request_remediation", fail_after_enqueue)
    response = send(case, evidence(definition))
    assert response.status_code == 409, response.text
    assert domain_state(case) == before
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 0


def test_expired_maintenance_lease_cannot_commit_an_admission(case):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    with case.database.scope(), connection() as database:
        task = claim(database)
    case.query(
        "UPDATE learning.background_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
        (task["id"],),
    )
    before = domain_state(case)
    with case.database.scope(), pytest.raises(SupersededWork), connection() as database:
        from tempo_learning_runtime.remediation_adapter import maintain_one

        payload = json.loads(task["payload_json"])
        maintain_one(database, payload)
        finish_slice(database, task, payload, False)
    assert domain_state(case) == before


@pytest.mark.parametrize("forgery", ["alternatives", "alias", "position"])
def test_intake_uses_learning_definitions_instead_of_producer_claimed_authority(
    case, forgery
):
    payload = bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [["e2e4"], ["d2d4"]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
        ]
    )
    definition = activate(case, payload)["publication"]["definitions"][0]
    delivery = evidence(definition)
    if forgery == "alternatives":
        delivery["comparisons"][0]["expected_moves"] = ["e2e4"]
    elif forgery == "alias":
        delivery["comparisons"][0]["card_id"] = "unaccepted-alias"
        delivery["obligations"][0]["card_id"] = "unaccepted-alias"
    else:
        delivery["encounters"][0]["position_key"] = "unsupported-position"
    response = send(case, delivery)
    assert response.status_code == 200, response.text
    assert response.json()["status"] in {"terminal_failure", "stale_revision"}
    assert response.json()["accepted_obligation_ids"] == []
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 0
    )


def test_repeated_remediation_arrivals_yield_to_ordinary_introductions(case):
    from test_game_evidence_runtime import activate_without_queue_work

    payload = opening_definitions(["e2e4", "d2d4", "c2c4"])
    activate_without_queue_work(case, payload)
    definition = payload["publication"]["definitions"][0]
    for revision in range(1, 5):
        accepted(case, evidence(definition, revision=revision))
        with case.database.scope():
            run_once()
            run_once()
    queued = {
        row[0]
        for row in case.query("SELECT DISTINCT card_id FROM learning.daily_queue")
    }
    assert queued == {fact["card_id"] for fact in payload["facts"]}
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 0


def test_single_card_slices_retain_library_miss_order_and_ordinary_relative_order(case):
    payload = opening_definitions(["e2e4", "d2d4", "c2c4", "b1c3"])
    activate(case, payload)
    definitions = payload["publication"]["definitions"]
    before = [
        row[0]
        for row in case.query(
            "SELECT card_id FROM learning.daily_queue ORDER BY position,id"
        )
    ]
    first = evidence(definitions[0])
    second = evidence(
        definitions[1],
        transport="second-delivery",
        evidence_id="evidence-b",
        game_id="game-b",
    )
    second["obligations"][0]["obligation_id"] = "obligation-b"
    accepted(case, first)
    accepted(case, second)
    drain(case)
    after = [
        row[0]
        for row in case.query(
            "SELECT card_id FROM learning.daily_queue ORDER BY position,id"
        )
    ]
    misses = sorted([definitions[0]["card_id"], definitions[1]["card_id"]])
    assert after[:2] == misses
    assert after[2:] == [card for card in before if card not in misses]


def test_reconciled_review_settles_through_the_original_issuance_frontier(case):
    definition = activate(case)["publication"]["definitions"][0]
    accepted(case, evidence(definition))
    drain(case)
    card = head(case)
    first = submit(case, card, review_payload(card, outcome="again"))
    assert first.status_code == 200, first.text
    assert (
        case.query("SELECT settled FROM learning.remediation_review_receipts")[0][0]
        is False
    )
    later = review_payload(card, recorded_at=datetime.now(timezone.utc).isoformat())
    response = submit(case, card, later, suffix="/reconcile")
    assert response.status_code == 200 and response.json()["persisted"], response.text
    assert response.json().get("reconciliation")
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        == 1
    )
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 2


@pytest.mark.parametrize("count", [64, 128], ids=["producer-scope", "wire-maximum"])
def test_bounded_evidence_batch_commits_all_contributions_and_one_receipt(case, count):
    definition = activate(case)["publication"]["definitions"][0]
    value = evidence(definition)
    first = deepcopy(value)
    for field in ["encounters", "comparisons", "misses", "obligations"]:
        value[field] = []
    for index in range(count):
        encounter = deepcopy(first["encounters"][0])
        encounter.update(encounter_id=f"encounter-{index}", ply=index)
        comparison = deepcopy(first["comparisons"][0])
        comparison["encounter_id"] = encounter["encounter_id"]
        miss = deepcopy(first["misses"][0])
        miss.update(encounter_id=encounter["encounter_id"], miss_id=f"miss-{index}")
        obligation = deepcopy(first["obligations"][0])
        obligation.update(
            encounter_id=encounter["encounter_id"],
            miss_id=miss["miss_id"],
            obligation_id=f"obligation-{index}",
        )
        value["encounters"].append(encounter)
        value["comparisons"].append(comparison)
        value["misses"].append(miss)
        value["obligations"].append(obligation)
    accepted(case, value)
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0]
        == count
    )
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 1


def test_intake_transaction_timeout_is_retryable_and_rolls_back_acceptance(
    case, monkeypatch
):
    definition = activate(case)["publication"]["definitions"][0]
    value = evidence(definition)
    execute = BorrowedConnection.execute

    def expire_transaction(self, statement, parameters=()):
        cursor = execute(self, statement, parameters)
        if statement.startswith("INSERT INTO remediation_contributions"):
            # PostgreSQL retains an already active timer when changing a
            # positive timeout; disable it before arming this fault deadline.
            self.database.execute_native("SET LOCAL transaction_timeout=0")
            self.database.execute_native("SET LOCAL transaction_timeout='10ms'")
            self.database.execute_native("SELECT pg_sleep(0.04)")
        return cursor

    with monkeypatch.context() as patch:
        patch.setattr(BorrowedConnection, "execute", expire_transaction)
        response = send(case, value)
        assert response.status_code == 503, response.text
        assert response.headers["Retry-After"] == "1"
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_contributions")[0][0] == 0
    )
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 0
    accepted(case, value)
