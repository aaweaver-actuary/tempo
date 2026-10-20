"""One explicit canonical game exported through bounded read-only owner ports."""
from datetime import datetime, timezone
import json
from copy import deepcopy

import pytest
from tempo_game_evidence_contracts.legacy import fingerprint
from app.legacy_game_observation import capture_game
from test_legacy_scoped_capture import source_fixture


def reader_for(source, prior=None):
    def read(statement, parameters=()):
        if 'FROM remediation_game_heads' in statement: return prior or []
        return source.read(statement, parameters)
    return read


def test_one_explicit_game_export_is_read_only_complete_and_keeps_full_physical_card():
    value, source = source_fixture()
    before = deepcopy(source.rows)
    capture = capture_game('ruy', value.game_id, reader=reader_for(source), metadata=source.metadata,
                           now=datetime(2026, 10, 18, 18, tzinfo=timezone.utc))
    assert capture['catalogue_scope']['scope']['decision_plies'] == [1, 3, 5]
    assert capture['game']['id'] == value.game_id and source.rows == before
    assert capture['tables']['cards'][0]['id'] == 'physical-black'
    assert len(json.loads(capture['tables']['cards'][0]['moves_json'])) == 6
    assert capture['export_digest'] == fingerprint({key: item for key, item in capture.items() if key != 'export_digest'})
    assert all('SELECT ' in statement for statement, _params in source.calls)


def test_existing_accepted_game_requires_original_saved_preview_not_new_revision_guess():
    value, source = source_fixture()
    with pytest.raises(ValueError, match='already_accepted_use_retained_preview'):
        capture_game('ruy', value.game_id, reader=reader_for(source, [dict(game_revision=1)]), metadata=source.metadata)
    assert not source.calls


@pytest.mark.parametrize('plies', [[], [5, 3], [True], list(range(129)), [1001]])
def test_invalid_or_unbounded_decision_scope_is_not_silently_sampled(plies):
    value, source = source_fixture()
    with pytest.raises(ValueError, match='decision_scope_exceeds_bound_or_is_invalid'):
        capture_game('ruy', value.game_id, plies, reader=reader_for(source), metadata=source.metadata)
