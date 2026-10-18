"""C3 dense real-source exports retain the cohort within immutable byte limits."""
import pytest
from tempo_game_evidence import analysis_export as owner


def records(kind, items):
    return {name: items if name == kind else [] for name in owner.KINDS}


def test_C3_small_pages_keep_original_64_item_canonical_bytes():
    items = [{'ordinal': index} for index in range(65)]
    pages = owner.pack_pages(records('positions', items), 'workspace', 'a' * 64)
    assert [len(page[3]['items']) for page in pages] == [64, 1]
    for index, page in enumerate(pages):
        expected = {'schema_version': 1, 'input_id': 'a' * 64,
                    'workspace_id': 'workspace', 'revision': 1,
                    'kind': 'positions', 'page_index': index,
                    'items': items[index * 64:(index + 1) * 64]}
        assert owner.canonical(page[3]) == owner.canonical(expected)
        assert page[2] == owner.digest(expected)
    assert owner.MAX_PAGE_BYTES == 8 * 1024 * 1024
    assert owner.MAX_EXPORT_BYTES == 64 * 1024 * 1024


def test_C3_dense_unicode_pages_split_by_actual_utf8_bytes_without_loss(monkeypatch):
    monkeypatch.setattr(owner, 'MAX_PAGE_BYTES', 768)
    items = [{'ordinal': index, 'explanation': 'é♞' * 30} for index in range(80)]
    pages = owner.pack_pages(records('comparisons', items), 'workspace', 'a' * 64)
    assert len(pages) > 2
    assert [item for page in pages for item in page[3]['items']] == items
    assert [page[1] for page in pages] == list(range(len(pages)))
    assert all(len(owner.canonical(page[3]).encode()) <= 768 for page in pages)
    assert all(page[2] == owner.digest(page[3]) for page in pages)


def test_C3_single_oversized_item_is_rejected_without_truncation(monkeypatch):
    monkeypatch.setattr(owner, 'MAX_PAGE_BYTES', 256)
    with pytest.raises(ValueError, match='analysis_page_byte_bound'):
        owner.pack_pages(records('positions', [{'value': 'x' * 256}]), 'workspace', 'a' * 64)


def test_C3_total_export_limit_still_applies(monkeypatch):
    monkeypatch.setattr(owner, 'MAX_EXPORT_BYTES', 256)
    with pytest.raises(ValueError, match='analysis_export_byte_bound'):
        owner.pack_pages(records('positions', [{'value': 'x' * 256}]), 'workspace', 'a' * 64)


def test_C3_encounter_scoping_retains_every_scope_reply_and_unknown_flag():
    selected = {'position_key': 'encountered', 'scopes': [
        {'policy_id': 'one', 'complete': True, 'replies': ['a', 'b']},
        {'policy_id': 'two', 'complete': False, 'replies': ['c']}]}
    inventories = [selected, {'position_key': 'unencountered', 'scopes': []}]
    original = owner.canonical(inventories)
    assert owner.applicable_inventories(inventories, {'encountered'}) == [selected]
    assert owner.canonical(inventories) == original


def test_C3_page_limit_includes_persisted_newline(monkeypatch):
    item = {'value': 'boundary'}
    page = {'schema_version': 1, 'input_id': 'a' * 64,
            'workspace_id': 'workspace', 'revision': 1,
            'kind': 'positions', 'page_index': 0, 'items': [item]}
    monkeypatch.setattr(owner, 'MAX_PAGE_BYTES', len(owner.canonical(page).encode()))
    with pytest.raises(ValueError, match='analysis_page_byte_bound'):
        owner.pack_pages(records('positions', [item]), 'workspace', 'a' * 64)
