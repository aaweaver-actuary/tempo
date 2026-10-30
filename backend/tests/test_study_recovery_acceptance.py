"""Owning acceptance-harness checks; no database or Docker allocation."""
import pytest

from test_product_game_lapse_postgres import postgres_fixture_profile


def test_recovery_profile_keeps_default_native_allocation_and_is_explicit():
    assert postgres_fixture_profile('default') == dict(memory_mib=384, storage='tmpfs')
    assert postgres_fixture_profile('study-recovery') == dict(memory_mib=1024, storage='owned-volume')
    for unsupported in (None, '', 'live', '1024', 'unlimited'):
        with pytest.raises(ValueError, match='unknown_owned_postgres_fixture_profile'):
            postgres_fixture_profile(unsupported)
