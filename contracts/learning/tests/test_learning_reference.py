from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from tempo_learning_contracts import v1
from .conftest import load_fixture, load_reference


def example():
    issuance = v1.AttemptIssuance.model_validate(load_fixture("attempt-issued"))
    submission = v1.ReviewSubmission.model_validate(load_fixture("review-submit"))
    return load_reference().ReferenceLearning(issuance), submission


def test_lost_response_retry_preserves_one_atomic_receipt_and_all_effects():
    receiver, submission = example()
    committed = receiver.receive(submission)
    replay = receiver.receive(submission)
    assert replay == committed
    assert replay.settlement.status == "committed"
    assert receiver.effects == {
        name: 1
        for name in (
            "reviews",
            "history",
            "schedule",
            "required_evidence",
            "receipts",
            "queue",
        )
    }


def test_same_submission_identity_changed_payload_conflicts_without_effects():
    receiver, submission = example()
    receiver.receive(submission, pending=True)
    conflicting = submission.model_copy(update={"outcome": "again"})
    result = receiver.receive(conflicting)
    assert result.settlement.code == "identity_payload_conflict"
    assert all(count == 0 for count in receiver.effects.values())


def test_pending_is_not_committed_and_same_key_can_settle():
    receiver, submission = example()
    pending = receiver.receive(submission, pending=True)
    assert pending.settlement.status == "accepted_pending"
    assert receiver.receive(submission, pending=True) == pending
    assert all(count == 0 for count in receiver.effects.values())
    assert receiver.receive(submission).settlement.status == "committed"


def test_concurrent_identical_submissions_settle_once():
    receiver, submission = example()
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies = list(executor.map(lambda _: receiver.receive(submission), range(32)))
    assert all(reply == replies[0] for reply in replies)
    assert set(receiver.effects.values()) == {1}


def test_concurrent_competing_payloads_have_one_winner():
    receiver, submission = example()
    competing = submission.model_copy(update={"outcome": "again"})
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(receiver.receive, [submission, competing]))
    assert sorted(reply.settlement.status for reply in replies) == [
        "committed",
        "conflict",
    ]
    assert set(receiver.effects.values()) == {1}


def test_another_submission_identity_cannot_review_same_attempt_twice():
    receiver, submission = example()
    receiver.receive(submission)
    reply = receiver.receive(
        submission.model_copy(update={"submission_id": "another-submission"})
    )
    assert reply.settlement.code == "concurrent_review_conflict"
    assert set(receiver.effects.values()) == {1}


def test_retirement_fences_unsettled_attempt_but_committed_receipt_replays():
    receiver, submission = example()
    receiver.receive(submission, pending=True)
    receiver.retire()
    assert receiver.receive(submission).settlement.code == "retired_publication"
    assert set(receiver.effects.values()) == {0}
    committed_receiver, committed_submission = example()
    committed = committed_receiver.receive(committed_submission)
    committed_receiver.retire()
    assert committed_receiver.receive(committed_submission) == committed


@pytest.mark.parametrize(
    "change,code",
    [
        ({"publication_generation": 2}, "publication_generation_changed"),
        ({"attempt_revision": 2}, "superseded_attempt"),
        ({"queue_revision": 2}, "queue_revision_changed"),
        ({"workspace_id": "other"}, "superseded_attempt"),
    ],
)
def test_stale_or_cross_workspace_attempt_never_commits(change, code):
    receiver, submission = example()
    assert (
        receiver.receive(submission.model_copy(update=change)).settlement.code == code
    )
    assert set(receiver.effects.values()) == {0}


def test_uncommitted_responses_cannot_claim_receipts():
    data = load_fixture("review-pending")
    data["settlement"]["receipt"] = load_fixture("review-committed")["settlement"][
        "receipt"
    ]
    with pytest.raises(ValidationError):
        v1.ReviewSettlement.model_validate(data)


def test_receipt_is_bound_to_submission_identity():
    data = load_fixture("review-committed")
    data["settlement"]["receipt"]["submission_id"] = "unrelated"
    with pytest.raises(ValidationError, match="another submission"):
        v1.ReviewSettlement.model_validate(data)


def test_pending_queue_cannot_smuggle_issued_attempts():
    data = load_fixture("queue-issued")
    data["status"] = "accepted_pending"
    with pytest.raises(ValidationError, match="only issued"):
        v1.QueueIssuance.model_validate(data)


def test_two_pending_submission_identities_cannot_both_settle_one_attempt():
    receiver, submission = example()
    other = submission.model_copy(update={"submission_id": "other-pending"})
    assert (
        receiver.receive(submission, pending=True).settlement.status
        == "accepted_pending"
    )
    assert receiver.receive(other, pending=True).settlement.status == "accepted_pending"
    assert receiver.receive(submission).settlement.status == "committed"
    assert receiver.receive(other).settlement.code == "concurrent_review_conflict"
    assert set(receiver.effects.values()) == {1}
