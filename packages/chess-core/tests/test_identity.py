import json
from pathlib import Path

import pytest
from tempo_chess_core import canonical_fen_key, card_id

TEST_ROOT = Path(__file__).parent
BASELINE = json.loads((TEST_ROOT / 'baseline.json').read_text())
SHARED = json.loads((TEST_ROOT / 'shared-card-id-parity.json').read_text())


@pytest.mark.parametrize('case', BASELINE, ids=[f'baseline-{index}' for index in range(len(BASELINE))])
def test_exact_baseline_digest_and_fen_key(case):
    assert canonical_fen_key(case['fen']) == case['fenKey']
    assert card_id(case['fen'], case['moves']) == case['cardId']


@pytest.mark.parametrize('case', SHARED, ids=lambda case: case['name'])
def test_existing_python_rust_digest_fixture_is_preserved(case):
    assert card_id(case['fen'], case['moves']) == case['cardId']


def test_clocks_and_whitespace_do_not_split_identity():
    assert card_id(' A w KQ e3 0 1 ', [' e4 ', 'c5']) == card_id('A\tw KQ e3 17 42', ['e4', ' c5 '])


def test_castling_en_passant_case_and_move_order_remain_distinct():
    original = card_id('A w KQ e3 0 1', ['e4', 'c5'])
    for fen, moves in [('A w K e3', ['e4', 'c5']), ('A w KQ -', ['e4', 'c5']),
                       ('a w KQ e3', ['e4', 'c5']), ('A w KQ e3', ['c5', 'e4']),
                       ('A w KQ e3', ['E4', 'c5'])]:
        assert card_id(fen, moves) != original


def test_malformed_and_empty_input_are_hashed_without_repair():
    assert canonical_fen_key(' invalid  fen ') == 'invalid fen'
    assert canonical_fen_key('') == ''
    assert card_id('', []) == '01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b'
    assert card_id('invalid', ['e4  e5']) != card_id('invalid', ['e4', 'e5'])


def test_installed_identity_package_exports_only_identity_without_runtime_dependencies():
    import importlib.metadata
    import tempo_chess_core
    assert 'site-packages' in tempo_chess_core.__file__
    assert tempo_chess_core.__all__ == ['canonical_fen_key', 'card_id']
    assert not importlib.metadata.requires('tempo-chess-core')
