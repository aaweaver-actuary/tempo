"""Offline one-game v3 production; v2 builder retains its independent tests."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from tempo_game_evidence_contracts import v3
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_game_evidence import preview_legacy_observation, normalize_pgn
from tempo_game_evidence.legacy_authority import authority_digest
from tempo_game_evidence.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'


def observation(kind=None):
    suffix = '-' + kind if kind else ''
    snapshot = json.loads((FIXTURES / ('legacy-scoped' + suffix + '-snapshot.json')).read_text())
    game = normalize_pgn((FIXTURES / 'legacy-authority-miss.pgn').read_bytes(), 'Learner')
    game.update(workspace_id='legacy', game_id=snapshot['catalogue_scope']['scope']['game_id'], revision=1)
    return dict(kind='legacy_game_observation', schema_version=1, game=game,
        game_source=dict(provider='lichess', provider_game_id='synthetic-game',
            content_hash=snapshot['catalogue_scope']['scope']['content_hash'], raw_source_digest=game['source_digest']),
        snapshot=snapshot, mapping={}, input_digest='a' * 64)


def test_generated_v3_keeps_exact_original_card_route_and_interior_decision():
    source = observation()
    original = deepcopy(source)
    preview = preview_legacy_observation(source)
    delivery = v3.EvidenceDelivery.model_validate(preview['delivery'])
    assert source == original and preview == preview_legacy_observation(source)
    assert preview['result'] == 'miss_obligation'
    assert [item['status'] for item in preview['findings']] == ['unknown', 'unknown', 'missed']
    comparison = delivery.comparisons[0]
    assert (comparison.card_id, comparison.card_revision, comparison.route_id,
            comparison.route_ply, comparison.exercise_ply) == ('physical-black', 1, 'ruy-route', 5, 5)
    assert delivery.encounters[0].observed_move == 'g8f6' and comparison.expected_moves == ['a7a6']
    assert delivery.authority.model_dump(mode='json') == source['snapshot']['authority']
    assert len(delivery.obligations) == len(delivery.misses) == 1 and not delivery.withdrawals
    assert 'publication_id' not in comparison.model_dump() and 'activation' not in delivery.model_dump()
    assert preview['preview_digest'] == fingerprint({key: item for key, item in preview.items() if key != 'preview_digest'})


@pytest.mark.parametrize('kind', ['unbound', 'foreign'])
def test_unknown_competing_source_never_manufactures_an_envelope(kind):
    preview = preview_legacy_observation(observation(kind))
    assert preview['delivery'] is None and preview['result'] == 'unknown'


def test_matching_selected_game_yields_no_miss_obligation_without_expanding_sample():
    source = observation()
    source['game']['moves'][-1] = 'a7a6'
    source['snapshot']['catalogue_scope']['scope']['moves'][-1] = 'a7a6'
    snapshot = source['snapshot']
    snapshot['capture_digest'] = authority_digest({key: value for key, value in snapshot.items() if key != 'capture_digest'})
    preview = preview_legacy_observation(source)
    assert preview['delivery'] is None and preview['result'] == 'no_miss_obligation'
    assert preview['game_id'] == source['game']['game_id'] and preview['findings'][-1]['status'] == 'matched'


@pytest.mark.parametrize('fault,reason', [('version', 'observation_required'), ('revision', 'initial_evidence_only'),
    ('content_hash', 'game_source_changed'), ('source_digest', 'game_source_changed'), ('moves', 'outside_decision_scope')])
def test_mixed_or_unsupported_one_game_source_is_rejected(fault, reason):
    source = observation()
    if fault == 'version': source['schema_version'] = True
    if fault == 'revision': source['game']['revision'] = 2
    if fault == 'content_hash': source['game_source']['content_hash'] = '0' * 64
    if fault == 'source_digest': source['game_source']['raw_source_digest'] = '0' * 64
    if fault == 'moves': source['game']['moves'][-1] = 'a7a6'
    with pytest.raises(ValueError, match=reason): preview_legacy_observation(source)


def test_offline_preview_cli_never_constructs_a_store_or_overwrites_output(tmp_path, monkeypatch):
    from tempo_game_evidence import cli
    monkeypatch.setattr(cli, 'EvidenceStore', lambda *a, **k: pytest.fail('Offline preview opened a domain store'))
    source, target = tmp_path / 'observation.json', tmp_path / 'preview.json'
    source.write_text(json.dumps(observation()))
    assert main(['legacy-preview', '--observation', str(source), '--output', str(target)]) == 0
    assert json.loads(target.read_text())['delivery']['contract_version'] == 3
    before = target.read_bytes()
    assert main(['legacy-preview', '--observation', str(source), '--output', str(target)]) == 1
    assert target.read_bytes() == before
