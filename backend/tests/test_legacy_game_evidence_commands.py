"""Synthetic authority/worker integration. PostgreSQL durability is a separate gate."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tempo_game_evidence_contracts import v3
from tempo_learning_remediation import ReceiveContext, RemediationError, validate_delivery

from app import command_gateway, game_evidence_commands as commands
from app.postgres_store import TempoRow
from app.services import legacy_game_authority as owner
from app.services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID, BRIDGE_MISS_REASON, owned_legacy_scope_sql

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    source = json.loads((ROOT / "services/game-evidence/fixtures/legacy-authority-input.json").read_text())
    tables = deepcopy(source["tables"])
    for row in tables["repertoires"]:
        row.update(is_main=int(row["id"] == "ruy"), scope_source_revision=2,
            canonical_prefix_revision=0, canonical_prefix_moves_json="[]", canonical_prefix_preview_id=None)
    for card in tables["cards"]:
        card.update(content_type="opening", superseded_by=None, repertoire_id="ruy", canonical_route_source=1)
    data = json.loads((ROOT / "contracts/game-evidence/fixtures/v3/delivery.json").read_text())["payload"]
    data["workspace_id"] = LEGACY_WORKSPACE_ID
    game = dict(id=data["game_id"], provider="lichess", provider_game_id="synthetic-game", content_hash="b" * 64,
        played_at="2026-10-17T12:00:00+00:00", color="black", result="draw",
        start_fen=tables["cards"][0]["start_fen"],
        moves_json='["e2e4","e7e5","g1f3","b8c6","f1b5","g8f6"]', adaptive_excluded=0)
    metadata = dict(scope_source_revision=2, canonical_prefix_revision=0, catalogue_generation=3,
        graph_generation=7, graph_state="ready", integrity_status="clean", scan_status="idle", scan_generation="synthetic-scan")
    captured = owner.Capture("synthetic-target", metadata, tables, deepcopy(source["graphs"]), game)
    return refresh(data, captured), captured


def refresh(value, captured):
    value = deepcopy(value)
    value["authority"] = owner.current_token(captured, "ruy").model_dump(mode="json")
    return v3.EvidenceDelivery.model_validate(value)


def prepared(value, captured):
    _, decisions = owner.prepare_decisions(value, captured)
    return commands.PreparedDelivery(value, captured, decisions)


def semantic_check(prepared):
    value = prepared.delivery
    return validate_delivery(value, ReceiveContext(LEGACY_WORKSPACE_ID, value.game_id, value.game_revision,
        value.evidence_id, value.evidence_revision, datetime(2026, 10, 19, tzinfo=timezone.utc),
        commands.CurrentLegacyAuthority(prepared)))


def test_backend_exact_source_projection_keeps_original_physical_card_and_complete_miss_authority():
    value, captured = fixture()
    before = deepcopy(captured)
    contributions = semantic_check(prepared(value, captured))
    assert captured == before
    assert len(contributions) == 1 and contributions[0].card_id == "physical-black"
    assert contributions[0].publication_id.startswith("legacy-graph:")
    payload = json.loads(contributions[0].payload_json)
    assert payload["authority"]["target_id"] == "synthetic-target"
    assert payload["comparison"]["exercise_ply"] == 5


@pytest.mark.parametrize("mapping", ["missing", "unsupported_revision"])
def test_backend_unmapped_authored_alternative_vetoes_even_a_forged_miss_delivery(mapping):
    value, captured = fixture()
    line = {**captured.tables["repertoire_lines"][0], "id": "ruy-alternative",
            "moves_json": captured.game["moves_json"]}
    captured.tables["repertoire_lines"].append(line)
    if mapping == "unsupported_revision":
        card = {**captured.tables["cards"][0], "id": "unsupported-card", "revision": 2,
                "moves_json": line["moves_json"]}
        captured.tables["cards"].append(card)
        captured.tables["repertoire_cards"].append({"repertoire_id": "ruy", "card_id": card["id"]})
        captured.graphs.append({**captured.graphs[0], "line_id": line["id"], "card_id": card["id"],
                               "moves_json": line["moves_json"]})
    value = refresh(value.model_dump(mode="json"), captured)
    result = prepared(value, captured)
    decision = next(iter(result.decisions.values()))
    assert decision.authored_moves == ("a7a6", "g8f6") and not decision.physical_binding_complete
    with pytest.raises(RemediationError, match="unknown_is_not_permission"):
        semantic_check(result)


def test_backend_distinct_physical_owners_for_same_authored_move_remain_ambiguous():
    value, captured = fixture()
    route = {**captured.tables["repertoire_lines"][0], "id": "duplicate-route"}
    card = {**captured.tables["cards"][0], "id": "different-physical"}
    captured.tables["repertoire_lines"].append(route)
    captured.tables["cards"].append(card)
    captured.tables["repertoire_cards"].append({"repertoire_id": "ruy", "card_id": card["id"]})
    captured.graphs.append({**captured.graphs[0], "line_id": route["id"], "card_id": card["id"]})
    value = refresh(value.model_dump(mode="json"), captured)
    with pytest.raises(RemediationError, match="unknown_is_not_permission"):
        semantic_check(prepared(value, captured))


def test_backend_ordinary_study_preserves_authority_identity_but_introduction_is_rechecked():
    value, captured = fixture()
    identity = value.authority.storage_identity()
    captured.tables["cards"][0].update(state="mature", due_date="2030-01-01", fsrs_card_json='{"changed":true}')
    captured.tables["reviews"] = [{"rating": "correct"}]
    assert owner.current_token(captured, "ruy").storage_identity() == identity
    assert semantic_check(prepared(value, captured))
    captured.tables["cards"][0]["introduced_at"] = None
    assert owner.current_token(captured, "ruy").storage_identity() == identity
    with pytest.raises(RemediationError, match="legacy_card_not_introduced"):
        semantic_check(prepared(value, captured))


@pytest.mark.parametrize("introduced,known", [("2026-10-16", True), ("2026-10-17", False),
    ("2026-10-17T11:00:00Z", True), ("2026-10-17T13:00:00Z", False),
    ("2026-10-17T12:00:00", False), ("invalid", False)])
def test_legacy_introduction_chronology_is_known_or_explicitly_unsupported(introduced, known):
    value, captured = fixture()
    captured.tables["cards"][0]["introduced_at"] = introduced
    result = prepared(value, captured)
    assert next(iter(result.decisions.values())).introduced_before_game is known
    if known: assert semantic_check(result)
    else:
        with pytest.raises(RemediationError, match="legacy_introduction_chronology_unknown"):
            semantic_check(result)


@pytest.mark.parametrize("fault,reason", [("provider", "game_source_changed"), ("time", "time_unknown"),
    ("move", "encounter_not_in_current_game"), ("prefix", "canonical_prefix_adapter_unsupported"),
    ("graph", "mixed_graph_capture")])
def test_backend_source_or_scope_changes_fail_closed(fault, reason):
    value, captured = fixture()
    if fault == "provider": captured.game["provider_game_id"] = "other"
    if fault == "time": captured.game["played_at"] = "2026-10-17"
    if fault == "move": captured.game["moves_json"] = '["e2e4","e7e5","g1f3","b8c6","f1b5","d7d5"]'
    if fault == "prefix": captured.tables["repertoires"][0]["canonical_prefix_moves_json"] = '["e2e4"]'
    if fault == "graph": captured.graphs[0]["generation"] = 8
    with pytest.raises(RemediationError, match=reason): prepared(value, captured)


def test_prepare_is_registered_outside_command_write_and_known_receipt_avoids_recapture(monkeypatch):
    value, captured = fixture()
    assert command_gateway._handlers["game_evidence.receive_legacy"] is commands.receive_legacy_delivery
    assert command_gateway._preparers["game_evidence.receive_legacy"] is commands.prepare_legacy_delivery
    monkeypatch.setattr(owner, "read_rows", lambda *args: [])
    monkeypatch.setattr(owner, "capture", lambda incoming: captured)
    result = commands.prepare_legacy_delivery({"delivery": value.model_dump(mode="json")})
    assert semantic_check(result)
    monkeypatch.setattr(owner, "read_rows", lambda *args: [dict(
        payload_hash=commands.digest(value.model_dump(mode="json")), response_json={"status": "completed"})])
    monkeypatch.setattr(owner, "capture", lambda *args: pytest.fail("successful receipt recaptured current source"))
    assert commands.prepare_legacy_delivery({"delivery": value.model_dump(mode="json")}).replay


def test_foreign_workspace_or_extra_prepared_values_never_reach_capture(monkeypatch):
    value, _ = fixture()
    monkeypatch.setattr(owner, "capture", lambda *args: pytest.fail("foreign source captured"))
    other = value.model_dump(mode="json")
    other["workspace_id"] = other["authority"]["workspace_id"] = "other"
    with pytest.raises(Exception) as error: commands.prepare_legacy_delivery({"delivery": other})
    assert error.value.status_code == 403
    with pytest.raises(Exception) as error: commands.prepare_legacy_delivery({"delivery": other, "prepared": {}})
    assert error.value.status_code == 422


class Writer:
    read_only = False

    def __init__(self, captured):
        self.captured, self.events = deepcopy(captured), []
        self.raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS")))
        self.generation = captured.metadata["catalogue_generation"]

    def execute_native(self, statement, parameters=()):
        self.events.append(statement)
        assert not any(term in statement.lower() for term in ("insert into daily_queue", "update cards", "insert into reviews"))
        data = None
        meta = self.captured.metadata
        if "FROM legacy_learning_identity" in statement: data = dict(target_id=self.captured.target_id, workspace_id=LEGACY_WORKSPACE_ID)
        elif "FROM repertoire_game_scope" in statement: data = {"generation": self.generation}
        elif "FROM repertoires WHERE" in statement: data = {key: meta[key] for key in ("scope_source_revision", "canonical_prefix_revision")}
        elif "FROM opening_graph_publications" in statement: data = dict(generation=meta["graph_generation"], state=meta["graph_state"])
        elif "FROM repertoire_integrity_state" in statement: data = dict(status=meta["integrity_status"], scan_status=meta["scan_status"], scan_generation=meta["scan_generation"])
        elif "FROM imported_games" in statement: data = self.captured.game
        elif statement.startswith("SELECT id FROM cards"): data = {"id": parameters[0]}
        elif "FROM cards WHERE" in statement: data = next(row for row in self.captured.tables["cards"] if row["id"] == parameters[0])
        row = TempoRow(tuple(data), tuple(data.values())) if data is not None else None
        return SimpleNamespace(fetchone=lambda: row)


def fake_repository(monkeypatch, writer, *, busy=False):
    class Repository:
        def __init__(self, connection, workspace, *, background):
            assert connection.info.transaction_status.name == "INTRANS"
            assert workspace == LEGACY_WORKSPACE_ID and background
            writer.events.append("module lock follows enclosing locks")

        def receive(self, value, context):
            status, reason, revision, accepted = "completed", None, 2, []
            try:
                accepted = [item.obligation_id for item in validate_delivery(value, context)]
            except RemediationError as error:
                status, reason, revision = error.status, error.reason, None
            if busy: status, reason, revision, accepted = "retryable_failure", "foreground_busy", None, []
            return v3.EvidenceAcknowledgement(contract_version=3, workspace_id=LEGACY_WORKSPACE_ID,
                delivery_id=value.delivery_id, evidence_id=value.evidence_id, evidence_revision=value.evidence_revision,
                status=status, reason=reason, learning_revision=revision, accepted_obligation_ids=accepted)

        def bind_attempt(self, *args): pytest.fail("intake issued an attempt")
        def settle(self, *args): pytest.fail("intake settled a review")
    monkeypatch.setattr(commands, "PostgresRepository", Repository)


def test_worker_receiver_preserves_card_before_module_lock_and_never_issues_or_reviews(monkeypatch):
    value, captured = fixture()
    writer = Writer(captured)
    fake_repository(monkeypatch, writer)
    result = commands.receive_legacy_delivery(writer, prepared(value, captured))
    assert result["status"] == "completed" and result["accepted_obligation_ids"] == ["legacy-obligation"]
    assert next(i for i, event in enumerate(writer.events) if "FOR UPDATE" in event) < writer.events.index("module lock follows enclosing locks")
    assert not any("daily_queue" in event or "reviews" in event for event in writer.events)


def test_worker_rechecks_source_before_acceptance_and_defers_busy_without_success(monkeypatch):
    value, captured = fixture()
    writer = Writer(captured)
    fake_repository(monkeypatch, writer)
    writer.generation += 1
    result = commands.receive_legacy_delivery(writer, prepared(value, captured))
    assert result["status"] == "stale_revision" and result["accepted_obligation_ids"] == []
    writer.generation -= 1
    fake_repository(monkeypatch, writer, busy=True)
    with pytest.raises(commands.BackgroundAdmissionDeferred):
        commands.receive_legacy_delivery(writer, prepared(value, captured))


def test_worker_fences_changed_introduction_chronology_without_changing_source_identity(monkeypatch):
    value, captured = fixture()
    writer = Writer(captured)
    writer.captured.tables["cards"][0]["introduced_at"] = "2026-10-20"
    assert owner.current_token(writer.captured, "ruy").storage_identity() == value.authority.storage_identity()
    fake_repository(monkeypatch, writer)
    result = commands.receive_legacy_delivery(writer, prepared(value, captured))
    assert result["status"] == "stale_revision" and result["accepted_obligation_ids"] == []


def test_native_legacy_priority_query_is_translatable_by_the_existing_owner():
    from app.postgres_store import postgres_sql
    from app.services.real_game_feedback import outstanding_miss_sql
    assert "remediation_contributions" in postgres_sql(outstanding_miss_sql(postgres=True))


def test_worker_cannot_bypass_the_read_only_guard():
    with pytest.raises(RuntimeError, match="command writer"):
        commands.receive_legacy_delivery(SimpleNamespace(read_only=True), None)
    with pytest.raises(RuntimeError, match="command writer"):
        commands.BorrowedTupleConnection(SimpleNamespace(read_only=True))


def test_legacy_scope_ownership_and_priority_reason_are_disjoint_from_weaker_path():
    from app.services.real_game_feedback import MISS_REASON, outstanding_miss_sql
    native, sqlite = outstanding_miss_sql(postgres=True), outstanding_miss_sql(postgres=False)
    assert "AND NOT " + owned_legacy_scope_sql() in native
    assert "remediation_" not in sqlite
    assert "workspace_id='legacy'" in native and "->>'repertoire_id'=event.repertoire_id" in native
    assert "head.active" not in owned_legacy_scope_sql()  # Withdrawn accepted scopes stay owned.
    assert BRIDGE_MISS_REASON != MISS_REASON


def test_source_pages_advance_with_a_finite_capture_bound(monkeypatch):
    rows = [dict(id=f"{index:03}") for index in range(130)]
    cursors = []
    def read(statement, parameters):
        cursor = parameters[-1]
        cursors.append(cursor)
        return [row for row in rows if row["id"] > cursor][:64]
    monkeypatch.setattr(owner, "read_rows", read)
    assert owner.paged_rows("synthetic read") == rows
    assert cursors == ["", "063", "127"]
    monkeypatch.setattr(owner, "MAX_SOURCE_ROWS", 128)
    with pytest.raises(RemediationError, match="legacy_capture_scope_exceeds_bound"):
        owner.paged_rows("synthetic read")


def test_source_capture_rejects_a_revision_change_between_closed_read_pages(monkeypatch):
    value, captured = fixture()
    readings = iter([captured.metadata, {**captured.metadata, "catalogue_generation": 4}])
    monkeypatch.setattr(owner, "metadata", lambda scope: next(readings))
    monkeypatch.setattr(owner, "paged_rows", lambda *args, **kwargs: [])
    def read(statement, parameters=()):
        if "legacy_learning_identity" in statement:
            return [dict(target_id=captured.target_id, workspace_id=LEGACY_WORKSPACE_ID)]
        if "imported_games" in statement: return [captured.game]
        return []
    monkeypatch.setattr(owner, "read_rows", read)
    with pytest.raises(RemediationError, match="legacy_source_changed_during_capture"):
        owner.capture(value)


def test_unknown_catalogue_color_cannot_supply_miss_authority():
    value, captured = fixture()
    captured.tables["repertoire_lines"][1]["trained_color"] = None
    with pytest.raises(RemediationError, match="legacy_catalogue_color_unknown"):
        prepared(value, captured)
