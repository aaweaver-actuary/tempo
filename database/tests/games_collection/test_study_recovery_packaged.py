"""Explicit Study recovery acceptance; does not enlarge collection-only gates."""
import inspect

import pytest

from test_packaged_collection import (
    case, owned_postgres,
    test_packaged_feedback_source_reaches_real_study_and_same_key_settlement as run_existing_feedback,
)


@pytest.fixture
def recovery_postgres(tmp_path_factory):
    yield from inspect.unwrap(owned_postgres)(tmp_path_factory, additional_ports=(80,), profile='study-recovery')


@pytest.fixture
def recovery_case(recovery_postgres):
    yield from inspect.unwrap(case)(recovery_postgres)


@pytest.mark.parametrize('study_only', [True, False], ids=['study-only-on', 'study-only-off'])
def test_packaged_study_recovery_thirty_consecutive_transitions(recovery_case, tmp_path, study_only):
    """Real source-bound v1 assembly; 30 measured saves per mode, no live target."""
    run_existing_feedback(recovery_case, tmp_path, introduced=True, recovery_mode=study_only)
