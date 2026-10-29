"""Task-owned legal fixtures exercise the Repertoire approval boundary."""
import json
from pathlib import Path
import sys

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/game-review-contracts/src", "packages/chess-core/src", "packages/evidence-math/src"):
    sys.path.insert(0, str(ROOT / source))
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire.changes.application import conflict_report, study_eligibility, require_valid_change
from app.repertoire.changes.ports import ChangeError


@pytest.fixture
def retained_import_case(monkeypatch):
    """Pure transport seam; real parser, segment generator and conflict policy."""
    from types import SimpleNamespace
    from psycopg.pq import TransactionStatus
    from app.chess.pgn import parse_pgn
    from app.command_gateway import request_digest
    from app import pgn_import_commands
    from app.repertoire.changes import retained
    from tempo_legacy_database.adapters.authored_repertoires import product_changes, product_imports
    from tempo_legacy_database.adapters.authored_repertoires.pgn_import import storage
    from tempo_legacy_database.adapters.commands.receipts import storage as receipts
    from tempo_legacy_database.adapters.postgres_store import TempoRow

    class Rows:
        def fetchall(self): return []

    class Database:
        raw = SimpleNamespace(info=SimpleNamespace(transaction_status=TransactionStatus.INTRANS))
        def execute_native(self, *_args): return Rows()

    parsed = parse_pgn('1. e4 c5 (1... e5) 2. Nf3 e6 *')
    lines = parsed[1] if isinstance(parsed, tuple) else parsed
    original = pgn_import_commands.prepare_import_payload('black-conflict.pgn', 'black', 2, 1, lines)
    original['operation_id'] = 'rejected-import'
    error = dict(status_code=409, detail=dict(code='repertoire_conflict', conflicts=['retained original evidence']))
    row = ['imports.pgn.explicit', request_digest('imports.pgn.explicit', original), 'failed', None,
        json.dumps(error), 1, None, None, None, json.dumps(original), 0, 1]
    state = SimpleNamespace(row=row, target=None, source=None, published=[], locks=[])
    monkeypatch.setattr(receipts, 'operation_status', lambda *_args: TempoRow(tuple(str(index) for index in range(12)), tuple(state.row)))
    monkeypatch.setattr(receipts, 'lock_operation', lambda *_args: state.locks.append('operation'))
    monkeypatch.setattr(storage, 'matching_repertoire', lambda *_args: (state.target,) if state.target else None)
    monkeypatch.setattr(storage, 'current_sources', lambda *_args: [])
    monkeypatch.setattr(storage, 'deleted_card_ids', lambda *_args: [])
    monkeypatch.setattr(storage, 'lock_main_repertoire', lambda *_args: state.locks.append('main'))
    monkeypatch.setattr(product_imports, 'current_snapshot', lambda *_args: state.source)
    monkeypatch.setattr(retained, 'lock_retained_sources', lambda *_args: state.locks.append('graph'))
    monkeypatch.setattr(product_changes, 'lock_sources', lambda *_args: state.locks.append('sources'))
    def publish(_database, payload):
        state.published.append(payload)
        return dict(repertoire_id=state.target or 'new-real-id', preparation_state='pending')
    monkeypatch.setattr(pgn_import_commands, 'explicit_pgn_import', publish)
    database = Database()
    owner = product_imports.RetainedImports(database)
    good = parse_pgn('1. e4 c5 2. Nf3 (2. Nc3 e6) e6 *')
    good_lines = good[1] if isinstance(good, tuple) else good
    return SimpleNamespace(owner=owner, database=database, state=state, lines=tuple(good_lines),
        conflicted=tuple(lines), original=original)


def test_retained_import_reopens_original_failed_evidence_after_reload(retained_import_case):
    from app.repertoire.changes.imports import retained_imports
    case = retained_import_case
    held = case.owner.read('rejected-import')
    assert retained_imports(case.database).read('rejected-import') == held
    assert json.loads(held.original_payload_json) == case.original
    assert json.loads(held.original_error_json)['detail']['conflicts'] == ['retained original evidence']
    assert case.state.published == [] and case.state.locks == []


def test_retained_import_preview_displays_exact_black_branches_and_new_cards(retained_import_case):
    case = retained_import_case
    preview = case.owner.preview('rejected-import', case.lines)
    body = json.loads(preview.approved_payload_json)
    assert [line['moves'] for line in body['lines']] == [line.moves for line in case.lines]
    assert preview.target_repertoire_id is None and preview.publication_status == 'not_saved'
    assert preview.display_digest == preview.content_digest()
    assert {change.presentation_id for change in preview.card_changes} == set(body['segment_ids'])
    assert all(change.action == 'add' and change.learner_color == 'black' for change in preview.card_changes)
    assert case.state.published == []
    edited = case.owner.preview('rejected-import', case.lines[:1])
    assert edited.display_digest != preview.display_digest


def test_retained_import_conflicting_or_invalid_edit_never_publishes(retained_import_case):
    from app.chess.pgn import ParsedLine
    from fastapi import HTTPException
    case = retained_import_case
    with pytest.raises(HTTPException) as error:
        case.owner.preview('rejected-import', case.conflicted)
    assert error.value.status_code == 409
    bad = ParsedLine(chess.STARTING_FEN, ['e2e4', 'e7e5', 'e2e3'], [])
    with pytest.raises((HTTPException, ValueError)):
        case.owner.preview('rejected-import', (bad,))
    assert case.state.published == []


@pytest.mark.parametrize('fault', ['source', 'target', 'evidence', 'payload', 'display', 'same_operation'])
def test_retained_import_approval_refuses_stale_or_changed_display(retained_import_case, fault):
    case = retained_import_case
    if fault == 'source':
        case.state.target = 'rep'
        case.state.source = snapshot(('a', ['e2e4', 'c7c5'], 'rep'))
    preview = case.owner.preview('rejected-import', case.lines)
    payload = preview.approval_payload('approved-import')
    if fault == 'source':
        case.state.source = case.state.source.model_copy(update={'membership': case.state.source.membership.model_copy(update={'revision': 2})})
    elif fault == 'target': case.state.target = 'created-after-preview'
    elif fault == 'evidence': case.state.row[9] = json.dumps(case.original | {'source_name': 'changed.pgn'})
    elif fault == 'payload': payload['depth'] = 3
    elif fault == 'display': payload['retained_import_preview']['card_changes'] = []
    else: payload['operation_id'] = 'rejected-import'
    with pytest.raises(ChangeError): case.owner.apply(payload)
    assert case.state.published == []


def test_retained_import_apply_uses_borrowed_importer_with_new_operation(retained_import_case):
    case = retained_import_case
    original = tuple(case.state.row)
    preview = case.owner.preview('rejected-import', case.lines)
    result = case.owner.apply(preview.approval_payload('approved-import'))
    assert result['preparation_state'] == 'pending'
    assert tuple(case.state.row) == original
    assert len(case.state.published) == 1
    assert case.state.published[0]['operation_id'] == 'approved-import'
    assert 'retained_import_preview' not in case.state.published[0]
    assert case.state.locks == ['operation', 'main']


def test_retained_import_command_reports_stale_approval_as_conflict(retained_import_case):
    from app.repertoire.changes.imports import apply_approved_import
    from fastapi import HTTPException
    case = retained_import_case
    preview = case.owner.preview('rejected-import', case.lines)
    case.state.target = 'created-after-preview'
    with pytest.raises(HTTPException) as error:
        apply_approved_import(case.database, preview.approval_payload('approved-import'))
    assert error.value.status_code == 409 and error.value.detail['code'] == 'stale_import_target'
    assert case.state.published == []


@pytest.mark.parametrize('approved', [False, True])
def test_explicit_import_retained_hook_validates_then_publishes_once(monkeypatch, approved):
    from app import pgn_import_commands as command
    from app.repertoire.changes import imports
    database = object()
    raw = {'operation_id': 'stable-approval'}
    if approved: raw['retained_import_preview'] = {'display': 'bound'}
    validated = {'operation_id': 'stable-approval', 'lines': ['exact approved source']}
    calls = []
    def validate(db, payload):
        assert db is database and payload is raw
        calls.append('validate')
        return validated
    def admit(db, payload, *, explicit):
        assert db is database and explicit
        assert payload is (validated if approved else raw)
        calls.append('admit')
        return {'preparation_state': 'pending'}
    monkeypatch.setattr(imports, 'validate_approved_import', validate)
    monkeypatch.setattr(command, '_admit_pgn_import', admit)
    # Invoke the command body; the existing refresh decorator surrounds it once.
    assert command.explicit_pgn_import.__wrapped__(database, raw) == {'preparation_state': 'pending'}
    assert calls == (['validate', 'admit'] if approved else ['admit'])


@pytest.mark.parametrize('fault', ['discarded', 'complete', 'non_conflict', 'wrong_command'])
def test_retained_import_requires_real_terminal_conflict_receipt(retained_import_case, fault):
    case = retained_import_case
    if fault == 'discarded': case.state.row[9] = None
    elif fault == 'complete': case.state.row[2] = 'complete'
    elif fault == 'non_conflict': case.state.row[4] = json.dumps(dict(status_code=422, detail='invalid source'))
    else: case.state.row[0] = 'cards.review'
    with pytest.raises(ChangeError): case.owner.read('rejected-import')


def oversized_conflicting_import(case):
    """Synthetic legal Black alternatives; no course or personal content."""
    from app import pgn_import_commands as command
    lines = [case.conflicted[index % 2] for index in range(1200)]
    payload = command.prepare_import_payload('synthetic-large-conflict.pgn', 'black', 2, 1, lines)
    payload['operation_id'] = 'rejected-import'
    rows = [dict(id=f'import:{index}', start_fen=line['starting_fen'],
        moves_json=json.dumps(line['moves']), trained_color='black')
        for index, line in enumerate(payload['lines'])]
    return payload, rows


def test_oversized_import_conflict_keeps_bounded_machine_diagnostics(retained_import_case):
    from app.pgn_import_validation import validate_sources
    from app.command_gateway import MAX_HTTP_ERROR_DETAIL_BYTES
    from fastapi import HTTPException
    case = retained_import_case
    payload, rows = oversized_conflicting_import(case)
    before = json.dumps(payload, sort_keys=True)
    with pytest.raises(HTTPException) as error:
        validate_sources('rep', rows)
    detail = error.value.detail
    assert error.value.status_code == 409 and detail['code'] == 'repertoire_conflict'
    assert len(json.dumps(detail, allow_nan=False).encode()) <= MAX_HTTP_ERROR_DETAIL_BYTES
    assert detail['conflict_count'] == 1 and detail['affected_route_count'] == 1200
    assert detail['conflicts_truncated'] is True
    assert json.dumps(payload, sort_keys=True) == before
    assert len(payload['lines']) == 1200 and case.state.published == []


def test_oversized_conflict_gateway_receipt_reopens_exact_import(retained_import_case, monkeypatch):
    from app import command_gateway as gateway, pgn_import_commands as command
    from test_command_receipt_storage_boundary import writer
    case = retained_import_case
    payload, _ = oversized_conflicting_import(case)
    digest = gateway.request_digest('imports.pgn.explicit', payload)
    pending = ('imports.pgn.explicit', digest, 'pending', None, None, None)
    # retained_import_case already substitutes the operation-lock transport.
    raw, _ = writer(monkeypatch, [], [None, pending])
    # Real importer strict guard and real gateway error projection; only the
    # borrowed SQL transport is the existing unit boundary harness.
    monkeypatch.setitem(gateway._handlers, 'imports.pgn.explicit',
        lambda database, value: command._admit_pgn_import(database, value, explicit=True))
    assert gateway.execute_command('rejected-import', 'imports.pgn.explicit', payload) is None
    statement, parameters = raw.calls[-1]
    assert 'error_json' in statement and parameters[1] == 'rejected-import'
    case.state.row[1], case.state.row[4], case.state.row[9] = digest, parameters[0], json.dumps(payload)
    held = case.owner.read('rejected-import')
    assert json.loads(held.original_error_json)['detail']['code'] == 'repertoire_conflict'
    assert json.loads(held.original_payload_json) == payload
    assert len(json.loads(held.original_payload_json)['lines']) == 1200
    # Reopening can produce a fresh explicit preview, never activate a winner.
    preview = case.owner.preview('rejected-import', case.lines)
    assert preview.retained == held and preview.publication_status == 'not_saved'
    assert case.state.published == []


def test_conflict_diagnostics_preserve_small_shape_and_whole_source_identities():
    from app.pgn_import_validation import MAX_CONFLICT_DIAGNOSTIC_BYTES, validate_sources
    from app.studies.repertoire_conflicts import conflicts_from_snapshot
    from fastapi import HTTPException
    rows = []
    for white in sorted(move.uci() for move in chess.Board().legal_moves):
        for _repeat in range(20):
            for black in ['c7c5', 'e7e5']:
                rows.append(dict(id=f'import:{len(rows)}', start_fen=chess.STARTING_FEN,
                    moves_json=json.dumps([white, black]), trained_color='black'))
    def complete(items):
        return conflicts_from_snapshot(tuple((row['id'], 'rep', '', 'black',
            row['start_fen'], row['moves_json']) for row in items))
    with pytest.raises(HTTPException) as small:
        validate_sources('rep', rows[:2])
    assert small.value.detail == dict(code='repertoire_conflict',
        message='Competing own responses need manual resolution; no content was saved.',
        conflicts=list(complete(rows[:2])))
    before = json.dumps(rows, sort_keys=True)
    full = complete(rows)
    with pytest.raises(HTTPException) as large:
        validate_sources('rep', rows)
    detail = large.value.detail
    assert len(json.dumps(detail, allow_nan=False).encode()) <= MAX_CONFLICT_DIAGNOSTIC_BYTES
    assert detail['conflict_count'] == len(full) == 20
    assert detail['affected_route_count'] == len(rows) == 800
    assert detail['conflicts_truncated'] is True and 0 < len(detail['conflicts']) < len(full)
    assert detail['conflicts'] == list(full[:len(detail['conflicts'])])
    assert json.dumps(rows, sort_keys=True) == before


def snapshot(*routes):
    values = []
    for identifier, moves, repertoire in routes:
        values.append(dict(route_id=identifier, repertoire_id=repertoire, policy_id=repertoire,
            learner_color="white", starting_fen=chess.STARTING_FEN, moves=moves, reentries=[]))
    return RepertoireSnapshot.model_validate_json(json.dumps(dict(
        membership=dict(workspace_id="task492", snapshot_id="fixture", revision=1,
            digest="a"*64, source_revision="source-1", captured_at="2026-10-26T12:00:00Z",
            scope_id="task492", scope_repertoire_ids=sorted({item[2] for item in routes}),
            complete=True, available=True, routes=values), variant="standard", learner_color="white",
        boundaries=[dict(route_id=item[0], terminal_ply=len(item[1]), deliberate=True) for item in routes],
        decision_presentations=[])))


def eligibility(value, route, repertoire="rep", ply=0, **changes):
    return study_eligibility(value, repertoire_id=repertoire, route_id=route, ply=ply,
        **(dict(source_revision="source-1", revision=1, digest="a"*64) | changes))


def test_same_repertoire_conflict_blocks_only_affected_occurrences():
    value = snapshot(("a", ["e2e4"], "rep"), ("b", ["d2d4"], "rep"),
        ("c", ["d2d4"], "other"))
    report = conflict_report(value)
    assert len(report.conflicts) == 1 and not report.clean
    assert report.conflicts[0].responses_uci == ("d2d4", "e2e4")
    assert {item.route_id for item in report.conflicts[0].occurrences} == {"a", "b"}
    denied = eligibility(value, "a")
    assert not denied.eligible and denied.suspend_without_lapse
    assert denied.reason == "own_response_conflict"
    assert eligibility(value, "c", "other").eligible


def test_opponent_fork_and_different_repertoire_choices_remain_valid():
    value = snapshot(("a", ["e2e4", "e7e5"], "rep"),
        ("b", ["e2e4", "c7c5"], "rep"), ("c", ["d2d4"], "other"))
    assert conflict_report(value).clean
    require_valid_change(value)


def test_transposed_own_conflict_retains_all_occurrences():
    value = snapshot(("a", ["g1f3", "d7d5", "g2g3", "c7c5", "f1g2"], "rep"),
        ("b", ["g2g3", "c7c5", "g1f3", "d7d5", "d2d4"], "rep"))
    report = conflict_report(value)
    assert any({(item.route_id, item.ply) for item in conflict.occurrences}
               == {("a", 4), ("b", 4)} for conflict in report.conflicts)


def test_invalid_tail_retains_diagnostic_and_never_certifies_prefix():
    value = snapshot(("bad", ["e2e4", "e7e5", "e2e3"], "rep"),
        ("good", ["d2d4"], "other"))
    report = conflict_report(value)
    assert not report.clean and any(item.code == "illegal_move" for item in report.diagnostics)
    assert eligibility(value, "bad").reason == "invalid_source"
    assert eligibility(value, "good", "other").eligible
    with pytest.raises(ChangeError, match="invalid_source"): require_valid_change(value)


def test_stale_or_unpublished_report_cannot_reopen_target():
    value = snapshot(("a", ["e2e4"], "rep"))
    assert eligibility(value, "a", revision=2).reason == "stale_integrity"
    raw = value.model_dump(mode="json"); raw["membership"]["available"] = False
    unpublished = RepertoireSnapshot.model_validate_json(json.dumps(raw))
    assert eligibility(unpublished, "a").reason == "unpublished_integrity"
    assert not conflict_report(unpublished).clean


from contextlib import contextmanager
from app.contracts.product_v02.analysis import Proposal, ProposalDecision
from app.repertoire.changes.application import (RepertoireChanges, manual_resolution,
    validate_proposal, validate_source_rows)


class MemoryStore:
    """Fake semantic owner; rollback models the all-or-nothing command boundary."""
    def __init__(self, value):
        self.source = value
        self.previews = {}
        self.receipts = {}
        self.published = []
        self.fail_publish = False

    @contextmanager
    def transaction(self, repertoire_id):
        before = self.source, dict(self.previews), dict(self.receipts), list(self.published)
        try: yield self
        except Exception:
            self.source, self.previews, self.receipts, self.published = before
            raise

    def snapshot(self): return self.source
    def card_definitions(self): return ()
    def preview(self, key): return self.previews.get(key)
    def save_preview(self, proposal): self.previews[proposal.proposal_id] = proposal
    def receipt(self, key): return self.receipts.get(key)
    def save_receipt(self, receipt): self.receipts[receipt.proposal_id] = receipt
    def publish(self, change):
        self.source = change.after
        self.published.append(change)
        if self.fail_publish: raise RuntimeError("owner publication failed")
        return change.after


def resolve_fixture():
    value = snapshot(("winner", ["e2e4", "e7e5", "g1f3"], "rep"),
        ("loser", ["d2d4", "d7d5", "c2c4"], "rep"),
        ("fork", ["e2e4", "c7c5", "g1f3"], "rep"),
        ("other", ["d2d4", "d7d5"], "other"))
    proposal = manual_resolution(value, repertoire_id="rep", position=chess.STARTING_FEN.rsplit(" ", 2)[0],
        selected_uci="e2e4", proposal_id="manual-492")
    return value, proposal


def decision(proposal, action="accept", edited=None):
    return ProposalDecision(proposal_id=proposal.proposal_id,
        expected_proposal_revision=proposal.proposal_revision,
        expected_source_revision=proposal.source_revision, display_digest=proposal.display_digest,
        action=action, edited_proposal=edited)


def rebind(proposal, **changes):
    value = proposal.model_copy(update=changes)
    return Proposal.model_validate_json(value.model_copy(update={"display_digest": value.content_digest()}).model_dump_json())


def test_manual_selection_previews_every_replacement_and_preserves_evidence_and_other_memberships():
    value, proposal = resolve_fixture()
    plan = validate_proposal(value, proposal)
    assert [item.route_id for item in proposal.edits] == ["loser"]
    assert proposal.edits[0].displayed_pgn == "1. e4 *"
    assert plan.before == value
    routes = {item.route_id: item.moves for item in plan.after.membership.routes}
    assert routes["loser"] == ("e2e4",)
    assert routes["winner"] == ("e2e4", "e7e5", "g1f3")
    assert routes["fork"] == ("e2e4", "c7c5", "g1f3")
    assert routes["other"] == ("d2d4", "d7d5")
    assert conflict_report(plan.after).clean
    # Original tails are retained, no physical card/history identity changed.
    assert value.membership.routes[1].moves == ("d2d4", "d7d5", "c2c4")


def test_acceptance_replay_has_one_effect_and_changed_payload_cannot_reuse_identity():
    value, proposal = resolve_fixture(); store = MemoryStore(value); service = RepertoireChanges(store)
    assert service.preview(proposal).status == "preview"
    receipt = service.accept_change(decision(proposal))
    assert service.accept_change(decision(proposal)) == receipt
    assert receipt.status == "accepted" and len(store.published) == 1
    changed = rebind(proposal, explanation="Changed displayed content")
    with pytest.raises(ChangeError, match="stale_or_unapproved_preview"):
        service.accept_change(decision(changed))
    with pytest.raises(ChangeError, match="already_decided"): service.preview(changed)


def test_edit_revalidates_and_requires_fresh_approval():
    value, proposal = resolve_fixture(); store = MemoryStore(value); service = RepertoireChanges(store)
    service.preview(proposal)
    edited = rebind(proposal, proposal_revision=2, explanation="Explicit edited explanation")
    assert service.edit_proposal(decision(proposal, "edit", edited)).status == "preview"
    assert not store.published
    with pytest.raises(ChangeError, match="stale_or_unapproved_preview"):
        service.accept_change(decision(proposal))
    assert service.accept_change(decision(edited)).status == "accepted"


def test_stale_source_and_publication_failure_have_no_partial_effect():
    value, proposal = resolve_fixture(); store = MemoryStore(value); service = RepertoireChanges(store)
    service.preview(proposal)
    raw = value.model_dump(mode="json"); raw["membership"]["digest"] = "b"*64
    store.source = RepertoireSnapshot.model_validate_json(json.dumps(raw))
    with pytest.raises(ChangeError, match="stale_source"): service.accept_change(decision(proposal))
    assert not store.receipts and not store.published
    store.source = value; store.fail_publish = True
    with pytest.raises(RuntimeError, match="publication failed"): service.accept_change(decision(proposal))
    assert store.source == value and not store.receipts and not store.published


def test_dismissal_has_no_content_or_learning_effect():
    value, proposal = resolve_fixture(); store = MemoryStore(value); service = RepertoireChanges(store)
    service.preview(proposal)
    receipt = service.dismiss_proposal(decision(proposal, "dismiss"))
    assert receipt.status == "dismissed" and store.source == value and not store.published
    assert service.dismiss_proposal(decision(proposal, "dismiss")) == receipt
    with pytest.raises(ChangeError, match="already_decided"): service.accept_change(decision(proposal))


def test_display_digest_and_full_route_changes_are_enforced():
    value, proposal = resolve_fixture()
    with pytest.raises(ChangeError, match="digest_mismatch"):
        validate_proposal(value, proposal.model_copy(update={"title": "Hidden changed title"}))
    changed_edit = proposal.edits[0].model_copy(update={"displayed_pgn": "1. e4 e5 *"})
    with pytest.raises(ChangeError, match="displayed_route_mismatch"):
        validate_proposal(value, rebind(proposal, edits=(changed_edit,)))


def test_new_conflicting_import_is_held_and_invalid_source_json_is_not_clean():
    rows = tuple(dict(source_id=name, start_fen=chess.STARTING_FEN,
        moves_json=json.dumps([move]), trained_color="white") for name, move in [("a", "e2e4"), ("b", "d2d4")])
    with pytest.raises(ChangeError, match="pending_resolution"): validate_source_rows("rep", rows)
    with pytest.raises(ChangeError, match="invalid_source"):
        validate_source_rows("rep", (dict(rows[0], moves_json="broken-json"),))


def engine_proposal():
    value = snapshot(("route-a", ["d2d4"], "white"))
    raw = json.loads((ROOT / "contracts/product-v02/fixtures/proposal.json").read_text())["payload"]
    raw["source_digest"] = value.membership.digest
    proposal = Proposal.model_validate_json(json.dumps(raw))
    return value, rebind(proposal)


def test_explicit_candidate_outside_top_five_uses_supplied_30cp_evidence_only():
    value, proposal = engine_proposal()
    assert proposal.edits[0].proposed_response_uci == "a2a3"
    assert validate_proposal(value, proposal).after.membership.routes[0].moves == ("a2a3",)
    evidence = proposal.engine_evidence[0]
    candidate = evidence.candidates[-1]
    score = candidate.score.model_copy(update={"value": evidence.best.score.value - 31})
    bad = evidence.model_copy(update={"candidates": evidence.candidates[:-1] + (candidate.model_copy(update={"score": score}),)})
    with pytest.raises(ChangeError, match="engine_acceptance_unproved"):
        validate_proposal(value, rebind(proposal, engine_evidence=(bad,)))


def test_engine_mate_inconclusive_or_wrong_history_never_manufactures_acceptance():
    value, proposal = engine_proposal(); evidence = proposal.engine_evidence[0]
    bad = evidence.model_copy(update={"context": evidence.context.model_copy(update={"history_uci": ("e2e4",)})})
    with pytest.raises(ChangeError, match="engine_context_mismatch"):
        validate_proposal(value, rebind(proposal, engine_evidence=(bad,)))
    raw = evidence.model_dump(mode="json"); raw["candidates"][-1]["score"] = None
    raw["candidates"][-1]["unavailable_reason"] = "fake_inconclusive"
    bad = type(evidence).model_validate_json(json.dumps(raw))
    with pytest.raises(ChangeError, match="engine_acceptance_unproved"):
        validate_proposal(value, rebind(proposal, engine_evidence=(bad,)))


def test_raw_pgn_parser_rejects_illegal_tail_instead_of_importing_recovered_prefix():
    from app.chess.pgn import parse_pgn
    with pytest.raises(ValueError, match="illegal tail"):
        parse_pgn('[Event "Task 492 invalid"]\n\n1. e4 e5 2. e4 *')
    games, lines = parse_pgn('[Event "Task 492 legal branches"]\n\n1. e4 (1. d4 d5) e5 2. Nf3 *')
    assert games == 1 and {tuple(line.moves) for line in lines} == {
        ("e2e4", "e7e5", "g1f3"), ("d2d4", "d7d5")}


def test_engine_free_unapproved_new_move_and_mixed_learner_sources_are_refused():
    value, proposal = engine_proposal()
    with pytest.raises(ChangeError, match="engine_evidence_required"):
        validate_proposal(value, rebind(proposal, engine_evidence=()))
    rows = tuple(dict(source_id=str(number), start_fen=chess.STARTING_FEN,
        moves_json='["e2e4"]', trained_color=color) for number, color in enumerate(("white", "black")))
    with pytest.raises(ChangeError, match="invalid_source"): validate_source_rows("rep", rows)


def test_http_routes_are_injected_and_approval_scope_is_enforced():
    from app.repertoire.changes.http import routes
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    value, proposal = resolve_fixture(); store = MemoryStore(value)
    class Snapshots:
        def snapshot(self, repertoire_id): return value
    api = FastAPI(); api.include_router(routes(lambda rep: RepertoireChanges(store), Snapshots()))
    with TestClient(api) as client:
        report = client.get('/api/product/repertoires/rep/conflicts')
        assert report.status_code == 200 and len(report.json()['conflicts']) == 1
        assert client.post('/api/product/repertoires/other/changes/preview',
            json=proposal.model_dump(mode="json")).status_code == 422
        assert client.post('/api/product/repertoires/rep/changes/preview',
            json=proposal.model_dump(mode="json")).json()['status'] == 'preview'
        assert client.post('/api/product/repertoires/rep/changes/decide',
            json=decision(proposal).model_dump(mode="json")).json()['status'] == 'accepted'


def test_resolving_one_conflict_preserves_unrelated_conflict_blocks():
    value = snapshot(("a", ["e2e4", "e7e5", "g1f3"], "rep"),
        ("b", ["d2d4"], "rep"), ("c", ["e2e4", "e7e5", "f1c4"], "rep"))
    proposal = manual_resolution(value, repertoire_id="rep", position=chess.STARTING_FEN.rsplit(" ", 2)[0],
        selected_uci="e2e4", proposal_id="one-conflict")
    after = validate_proposal(value, proposal).after
    assert len(conflict_report(after).conflicts) == 1
    assert eligibility(after, "b", source_revision=after.membership.source_revision,
        revision=after.membership.revision, digest=after.membership.digest).eligible
    assert eligibility(after, "a", ply=2, source_revision=after.membership.source_revision,
        revision=after.membership.revision, digest=after.membership.digest).reason == "own_response_conflict"


def test_engine_mate_and_undisplayed_card_additions_cannot_be_accepted():
    value, proposal = engine_proposal()
    evidence = proposal.engine_evidence[0]
    raw = evidence.model_dump(mode="json")
    # Reuse the shared published mate shape, rather than inventing a score DTO.
    mate = json.loads((ROOT / "contracts/product-v02/fixtures/engine-mate.json").read_text())["payload"]
    raw["candidates"][-1]["score"] = mate["best"]["score"]
    bad = type(evidence).model_validate_json(json.dumps(raw))
    with pytest.raises(ChangeError, match="engine_acceptance_unproved"):
        validate_proposal(value, rebind(proposal, engine_evidence=(bad,)))
    card = proposal.card_changes[0].model_copy(update={"moves": ("h2h4",)})
    with pytest.raises(ChangeError, match="card_outside_displayed_routes"):
        validate_proposal(value, rebind(proposal, card_changes=(card,)))


def source_row(source="line:old", move="e2e4"):
    return dict(source_id=source, start_fen=chess.STARTING_FEN,
        moves_json=json.dumps([move]), trained_color="white")


def test_legacy_import_guard_stops_conflict_before_any_source_write(monkeypatch):
    from app import pgn_import_commands as command
    from fastapi import HTTPException
    monkeypatch.setattr(command.storage, "lock_main_repertoire", lambda *_: None)
    monkeypatch.setattr(command.storage, "matching_repertoire", lambda *_: ("rep",))
    monkeypatch.setattr(command.storage, "current_sources", lambda *_: (dict(source_row(), id="line:old"),))
    payload = dict(source_name="task492.pgn", trained_color="white", depth=3, games_found=1,
        unique_lines=1, imported_segments_count=1, decision_segments_count=1, prefix_segments_count=0,
        segment_ids=[], prefix_segment_ids=[], lines=[dict(starting_fen=chess.STARTING_FEN,
            moves=["d2d4"], annotations=[])])
    with pytest.raises(HTTPException) as error:
        command.admit_pgn_import.__wrapped__(object(), payload)
    assert error.value.status_code == 409 and error.value.detail['code'] == "repertoire_conflict"


def test_legacy_branch_guard_stops_conflict_before_insert(monkeypatch):
    from app import branch_commands as command
    from fastapi import HTTPException
    monkeypatch.setattr(command.storage, "lock_repertoire_for_add", lambda *_: ("rep",))
    monkeypatch.setattr(command.change_storage, "lock_sources", lambda *_: (source_row(),))
    with pytest.raises(HTTPException) as error:
        command.add_repertoire_branch.__wrapped__(object(), dict(repertoire_id="rep", name="task492",
            trained_color="white", starting_fen=chess.STARTING_FEN, moves=["d2d4"]))
    assert error.value.status_code == 409 and error.value.detail == "conflict_pending_resolution"


def test_legacy_card_edit_guard_keeps_shared_membership_before_writes(monkeypatch):
    from app import card_commands as command
    from app.services import canonical_prefix
    from fastapi import HTTPException
    monkeypatch.setattr(command.storage, "card_scope", lambda *_: [("rep",)])
    monkeypatch.setattr(canonical_prefix, "ensure_line_in_scope", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(command.change_storage, "lock_sources", lambda *_: (source_row(), source_row("card:card")))
    with pytest.raises(HTTPException) as error:
        command.revise_card.__wrapped__(object(), dict(card_id="card", request=dict(
            starting_fen=chess.STARTING_FEN, moves=["d2d4"], expected_revision=1, history_mode="preserve")))
    assert error.value.status_code == 409 and error.value.detail == "conflict_pending_resolution"


def test_legacy_integrity_repair_cannot_introduce_new_response_conflict(monkeypatch):
    from app import integrity_repair_commands as command
    from app.services import canonical_prefix
    from fastapi import HTTPException
    monkeypatch.setattr(command, "request_graph_rebuild_in_transaction", lambda *_: {"id": "task"})
    monkeypatch.setattr(command.storage, "locked_repertoire", lambda *_: ("rep",))
    monkeypatch.setattr(command.storage, "locked_issue_signature", lambda *_: ("signature",))
    monkeypatch.setattr(command.storage, "locked_source_line", lambda *_: dict(
        id="old", repertoire_id="rep", start_fen=chess.STARTING_FEN, moves_json='["e2e4"]'))
    monkeypatch.setattr(canonical_prefix, "ensure_line_in_scope", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(command.change_storage, "lock_sources", lambda *_: (source_row(), source_row("card:card")))
    with pytest.raises(HTTPException) as error:
        command.resolve_integrity_issue.__wrapped__(object(), dict(repertoire_id="rep", issue_id="issue",
            signature="signature", changed_lines={"old": ["d2d4"]}, changed_cards={},
            expected_line_moves={"old": '["e2e4"]'}, expected_card_moves={}, unsupported_card_ids=[]))
    assert error.value.status_code == 409 and error.value.detail == "conflict_pending_resolution"


def test_approval_for_displayed_addition_passes_exact_cards_to_owner_without_learning_write():
    value, proposal = engine_proposal(); store = MemoryStore(value); changes = RepertoireChanges(store)
    changes.preview(proposal); receipt = changes.accept_change(decision(proposal))
    assert receipt.status == "accepted"
    assert store.published[0].proposal.card_changes == proposal.card_changes
    assert len(store.published[0].proposal.card_changes) == 1


def test_maximum_proposal_identifier_remains_usable_and_accepts_only_displayed_key():
    value, proposal = resolve_fixture(); proposal = rebind(proposal, proposal_id="x"*200)
    store = MemoryStore(value); changes = RepertoireChanges(store)
    changes.preview(proposal)
    receipt = changes.accept_change(decision(proposal))
    assert len(receipt.owner_receipt_id) < 200
    assert changes.accept_change(decision(proposal)) == receipt


def test_retained_publication_uses_owner_revision_and_requires_exact_approved_content():
    from app.repertoire.changes.application import verify_retained_publication
    value, proposal = resolve_fixture(); change = validate_proposal(value, proposal)
    payload = change.after.model_dump(mode='json')
    payload['membership']['revision'] = 9
    payload['membership']['routes'][0]['route_id'] = 'canonical-replacement-id'
    payload['boundaries'][0]['route_id'] = 'canonical-replacement-id'
    published = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    verify_retained_publication(change,published)
    payload['membership']['routes'][0]['moves'] = ['h2h4']
    payload['boundaries'][0]['terminal_ply'] = 1
    wrong = RepertoireSnapshot.model_validate_json(json.dumps(payload))
    with pytest.raises(ChangeError,match='retained_publication_mismatch'):
        verify_retained_publication(change,wrong)


def test_retained_reader_is_the_published_snapshot_owner_callable():
    from app.repertoire.changes import retained
    from tempo_legacy_database.adapters.authored_repertoires import product_snapshot
    assert retained.current_snapshot is product_snapshot.current_snapshot
    assert retained._shared_snapshot_reader is product_snapshot.current_snapshot
