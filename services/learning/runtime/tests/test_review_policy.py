"""Review policy borrows the transaction already holding submission locks."""

from types import SimpleNamespace

import pytest
import tempo_learning_review as learning
from tempo_learning_runtime import database as runtime_database, review_adapter


@pytest.mark.parametrize(
    "timezone,first_interval_days", [("America/New_York", 3), ("UTC", 1)]
)
def test_review_policy_reads_borrowed_settings_without_nested_connection(
    monkeypatch, timezone, first_interval_days
):
    reads = []

    class BorrowedTransaction:
        def execute(self, statement, parameters=()):
            reads.append((statement, parameters))
            return SimpleNamespace(
                fetchone=lambda: {
                    "timezone": timezone,
                    "light_first_interval_days": first_interval_days,
                }
            )

    def forbid_connection(*arguments, **keywords):
        raise AssertionError("Review policy must borrow the locked command transaction")

    # Guard both the imported legacy alias and the acquisition owner's capability.
    monkeypatch.setattr(review_adapter, "read_connection", forbid_connection, raising=False)
    monkeypatch.setattr(review_adapter, "connection", forbid_connection)
    monkeypatch.setattr(runtime_database, "connection", forbid_connection)
    monkeypatch.setattr(runtime_database, "read_connection", forbid_connection)
    adapter = review_adapter.LearningReviewAdapter(BorrowedTransaction(), None)

    assert adapter.policy() == learning.ReviewPolicy(timezone, first_interval_days)
    assert len(reads) == 1


def test_review_policy_preserves_injected_provider_without_database_read():
    class UnavailableTransaction:
        def execute(self, *arguments, **keywords):
            raise AssertionError("Injected review policy must not read persistent settings")

    settings = SimpleNamespace(timezone="UTC", light_first_interval_days=2)
    calls = []

    def policy_provider():
        calls.append(True)
        return settings

    adapter = review_adapter.LearningReviewAdapter(
        UnavailableTransaction(), None, policy_provider=policy_provider
    )
    assert adapter.policy() == learning.ReviewPolicy("UTC", 2)
    assert calls == [True]
