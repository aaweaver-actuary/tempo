"""Source vertical fixture: real legacy queue/review writer, scripted module SQL.

This is not PostgreSQL durability/locking proof. The script supplies fixed owner
rows and emulates module SQL effects; qualifying outcome logic executes in the
real installed-library source. Native execution remains a separate finite gate.
"""
from datetime import date, datetime, timedelta, timezone
from contextlib import nullcontext, asynccontextmanager
import asyncio
import json
from types import SimpleNamespace

import pytest
from tempo_learning_remediation import PostgresRepository, ReceiveContext

from app import database, queue_commands
from app.postgres_store import PostgresConnection, TempoRow
from app.services import legacy_study_remediation as bridge
from app.services.learning_review_adapter import LearningReviewAdapter, submit_legacy_review
from app.services.legacy_game_evidence_scope import BRIDGE_MISS_REASON
from test_legacy_scoped_capture import source_fixture, captured_scope, prepared, semantic_check, integrity_row
from app.game_evidence_commands import CurrentLegacyAuthority


class Cursor:
    def __init__(self, rows=()): self.rows, self.rowcount = list(rows), len(rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class ModuleScript:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))
        self.sequence, self.attempts, self.receipts, self.settlements, self.log = 0, {}, {}, {}, []
        self.admission = None
        self.workspace_revision, self.deliveries, self.evidence, self.contributions = 0, {}, {}, {}
        self.sources, self.games, self.heads, self.incidents = {}, {}, {}, {}

    @property
    def outstanding(self):
        return self.sequence > 0 and not any(not invalid and self.attempts[attempt][-1] >= self.sequence
                       for attempt, invalid in self.settlements.values())

    def execute(self, statement, parameters=()):
        self.log.append((statement, parameters))
        if "pg_advisory" in statement: return Cursor([(True,)])
        if statement.startswith("SELECT EXISTS"):
            # Fixed fixture query oracle; native tests own this SQL's semantics.
            return Cursor([(self.outstanding,)])
        if "FROM remediation_deliveries" in statement:
            value = self.deliveries.get(parameters[1])
            if statement.startswith("SELECT response_json"): value = (value[1],) if value else None
            return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_deliveries"):
            self.deliveries[parameters[1]] = (parameters[2], json.loads(parameters[3])); return Cursor()
        if "FROM remediation_evidence" in statement:
            value = self.evidence.get(tuple(parameters[1:])); return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_evidence"):
            self.evidence[tuple(parameters[1:3])] = tuple(parameters[-2:]); return Cursor()
        if "FROM remediation_source_heads" in statement:
            value = self.sources.get(parameters[1]); return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_source_heads"):
            self.sources[parameters[1]] = tuple(parameters[2:]); return Cursor()
        if "FROM remediation_game_heads" in statement and not statement.startswith("SELECT EXISTS"):
            value = self.games.get(parameters[1]); return Cursor([(value,)] if value else [])
        if statement.startswith("INSERT INTO remediation_game_heads"):
            self.games[parameters[1]] = parameters[2]; return Cursor()
        if "FROM remediation_contributions" in statement:
            value = self.contributions.get(tuple(parameters[1:])); return Cursor([(value,)] if value else [])
        if statement.startswith("INSERT INTO remediation_contributions"):
            self.contributions[tuple(parameters[1:3])] = parameters[14]; return Cursor()
        if "FROM remediation_obligation_heads" in statement:
            value = self.heads.get(parameters[1]); return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_obligation_heads"):
            self.heads[parameters[1]] = (*parameters[3:6], parameters[2], True); return Cursor()
        if "FROM remediation_incident_versions" in statement:
            value = self.incidents.get(tuple(parameters[1:])); return Cursor([(value,)] if value else [])
        if statement.startswith("INSERT INTO remediation_incident_versions"):
            self.incidents[tuple(parameters[1:3])] = parameters[4]; return Cursor()
        if statement.startswith(("INSERT INTO remediation_workspaces", "INSERT INTO remediation_cards")): return Cursor()
        if statement.startswith("UPDATE remediation_workspaces SET revision"):
            self.workspace_revision += 1; return Cursor([(self.workspace_revision,)])
        if statement.startswith("UPDATE remediation_cards SET sequence"):
            self.sequence += 1; return Cursor([(self.sequence,)])
        if "SELECT sequence FROM remediation_cards" in statement: return Cursor([(self.sequence,)])
        if "FROM remediation_attempts WHERE" in statement:
            value = self.attempts.get(parameters[1])
            return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_attempts"):
            self.attempts[parameters[1]] = tuple(parameters[2:])
            return Cursor()
        if "FROM remediation_review_receipts" in statement:
            value = self.receipts.get(parameters[1])
            return Cursor([value] if value else [])
        if statement.startswith("INSERT INTO remediation_settlements"):
            self.settlements[parameters[1]] = (parameters[2], False)
            return Cursor()
        if statement.startswith("INSERT INTO remediation_review_receipts"):
            self.receipts[parameters[1]] = tuple(parameters[2:])
            return Cursor()
        if statement.startswith("UPDATE remediation_settlements"):
            if parameters[2] in self.settlements:
                attempt, _ = self.settlements[parameters[2]]
                self.settlements[parameters[2]] = (attempt, True)
            return Cursor()
        if statement.startswith("UPDATE remediation_cards SET work_state"):
            self.admission = parameters[:3]
            return Cursor()
        pytest.fail("Unscripted module SQL: " + statement)


class OwnerFixture(PostgresConnection):
    def __init__(self, sqlite, payload, module):
        self.sqlite, self.payload, self.module, self.read_only = sqlite, payload, module, False
        self.log, self.current, self.busy = [], True, False
        self.sources_current = True

    def execute(self, statement, parameters=()):
        self.log.append((statement, parameters))
        if "current_repertoire_decision_events" in statement and "bridge_scope" in statement: return Cursor()
        statement = statement.replace(" FOR UPDATE OF q,c", "").replace(" FOR UPDATE", "").replace(" FOR SHARE", "")
        return self.sqlite.execute(statement, parameters)

    def execute_native(self, statement, parameters=()):
        self.log.append((statement, parameters))
        if statement == bridge.CURRENT_FACT_SQL:
            return Cursor([TempoRow(("payload_json", "introduced_at"), (self.payload, "2026-10-16"))] if self.current else [])
        if statement == bridge.SOURCE_FENCE_SQL:
            return Cursor([TempoRow(("game_id", "source_current"),
                (json.loads(self.payload)["game_id"], self.sources_current))])
        if statement.startswith("SELECT id FROM imported_games WHERE id IN"):
            return Cursor([(json.loads(self.payload)["game_id"],)])
        if "pg_try_advisory_xact_lock_shared" in statement: return Cursor([(not self.busy,)])
        if "pg_advisory" in statement: return Cursor([(True,)])
        if "FOR SHARE OF repertoire" in statement: return Cursor([(1,)])
        if "FROM repertoire_integrity_state state" in statement:
            values = integrity_row()
            return Cursor([TempoRow(tuple(values), tuple(values.values()))])
        if "FROM remediation_attempts WHERE" in statement:
            value = self.module.attempts.get(parameters[1])
            columns = ("attempt_id", "card_id", "card_revision", "publication_id", "publication_generation", "issued_at")
            return Cursor([TempoRow(columns, (parameters[1], *value[:5]))] if value else [])
        if "SELECT target_id,workspace_id FROM legacy_learning_identity" in statement:
            return Cursor([("synthetic-target", "legacy")])
        if "SELECT sequence,work_state,reason,queue_entry_id FROM remediation_cards" in statement:
            admission = self.module.admission or ("pending", None, None)
            return Cursor([TempoRow(("sequence", "work_state", "reason", "queue_entry_id"),
                                   (self.module.sequence, *admission))])
        if "SELECT COUNT(*) FROM remediation_obligation_heads" in statement: return Cursor([(1,)])
        if "SELECT payload_json FROM remediation_contributions" in statement: return Cursor([(self.payload,)])
        if "remediation_" not in statement:
            return self.execute(statement.replace("%s", "?"), parameters)
        pytest.fail("Unscripted owner SQL: " + statement)


@pytest.fixture
def vertical(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "synthetic-study.db")
    database.initialize()
    value, source = source_fixture()
    value, captured = captured_scope(value, source)
    accepted = semantic_check(prepared(value, captured))[0]
    module = ModuleScript()
    context = ReceiveContext("legacy", value.game_id, value.game_revision, value.evidence_id,
        value.evidence_revision, datetime.now(timezone.utc), CurrentLegacyAuthority(prepared(value, captured)))
    owned = PostgresRepository(module, "legacy")
    first = owned.receive(value, context)
    assert first.status == "completed" and owned.receive(value, context) == first
    assert module.sequence == module.workspace_revision == len(module.contributions) == len(module.deliveries) == 1
    assert next(iter(module.contributions.values())) == accepted.payload_json
    monkeypatch.setattr(bridge, "repository", lambda _database: PostgresRepository(module, "legacy"))
    # Task wakeups are outside this source fixture; no worker/service is started.
    monkeypatch.setattr(LearningReviewAdapter, "refresh_progression", lambda *a, **k: None)
    monkeypatch.setattr(LearningReviewAdapter, "refresh_priority", lambda *a, **k: None)
    with database.connection() as sqlite:
        sqlite.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('ruy','Synthetic','fixture',?)",
                       (date.today().isoformat(),))
        for card_id, content, moves, color in (("physical-black", "opening", source.rows["cards"][0]["moves_json"], "black"),
            ("protected", "tactic", '["e2e4"]', "white"), ("ordinary", "tactic", '["d2d4"]', "white")):
            sqlite.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,"
                "introduced_at,content_type) VALUES(?,'ruy','prefix',?,?,?,'learning',?,'2026-10-16',?)",
                (card_id, captured.game["start_fen"], moves, color, date.today().isoformat(), content))
        for position, card_id in enumerate(("protected", "ordinary")):
            sqlite.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
                           (date.today().isoformat(), card_id, position))
        sqlite.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id='protected'")
        yield OwnerFixture(sqlite, accepted.payload_json, module), value, accepted, module


def issue(database, card_id, entry):
    return queue_commands.issue_queue_head(database, dict(entry_id=entry, card_id=card_id,
        expected_revision=1, queue_date=date.today().isoformat()))


def complete(database, entry, *, attempt_id="synthetic-review", outcome="correct", guided=False, recorded_at=None):
    return submit_legacy_review(database, dict(card_id="physical-black", review=dict(outcome=outcome,
        guided=guided, queue_entry_id=entry, expected_revision=1, attempt_id=attempt_id, recorded_at=recorded_at)))


def test_synthetic_accepted_physical_card_uses_normal_study_issuance_and_exactly_once_review(vertical):
    db, value, accepted, module = vertical
    before_other = [tuple(row) for row in db.sqlite.execute("SELECT * FROM cards WHERE id<>'physical-black' ORDER BY id")]
    current = bridge.current_fact(db, "physical-black")
    assert current.card_id == accepted.card_id and current.authority == value.authority
    result = bridge.admit_card(db, {"card_id": "physical-black"})
    assert result["status"] == "applied"
    entry = result["queue_entry_id"]
    assert [row[0] for row in db.execute("SELECT card_id FROM daily_queue ORDER BY position,id")] == ["protected", "physical-black", "ordinary"]
    assert not module.attempts and db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    # A preview/prefetched later entry is not the actual head and cannot bind.
    assert issue(db, "physical-black", entry) == {"issued": False}
    assert not module.attempts
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    assert issue(db, "physical-black", entry) == {"issued": True}
    binding = next(iter(module.attempts.values()))
    assert binding[-1] == 1 and binding[:4] == ("physical-black", 1, *value.authority.storage_identity())
    assert issue(db, "physical-black", entry) == {"issued": True}
    assert list(module.attempts.values()) == [binding]
    first = complete(db, entry)
    assert first["persisted"] and first["review_id"] is not None
    assert len(module.receipts) == len(module.settlements) == 1 and not module.outstanding
    assert complete(db, entry)["review_id"] == first["review_id"]
    assert len(module.receipts) == len(module.settlements) == 1
    assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='physical-black'").fetchone()[0] == 1
    assert before_other == [tuple(row) for row in db.sqlite.execute("SELECT * FROM cards WHERE id<>'physical-black' ORDER BY id")]
    db.execute("UPDATE reviews SET invalidated_at=?,invalidation_reason='synthetic correction' WHERE id=?",
               (datetime.now(timezone.utc).isoformat(), first["review_id"]))
    bridge.invalidate_review(db, "physical-black", first["review_id"], "synthetic correction")
    assert module.outstanding and len(module.receipts) == len(module.settlements) == 1


@pytest.mark.parametrize("fence", ["already_issued", "stale", "busy", "corrected_source"])
def test_prior_offline_or_stale_authority_never_acquires_retrospective_binding(vertical, fence):
    db, _value, _accepted, module = vertical
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    if fence == "already_issued": db.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE card_id='physical-black'")
    if fence == "stale": db.current = False
    if fence == "busy": db.busy = True
    if fence == "corrected_source": db.sources_current = False
    assert issue(db, "physical-black", entry)["issued"]
    assert not module.attempts
    complete(db, entry)
    assert not module.receipts and not module.settlements and module.outstanding


@pytest.mark.parametrize("outcome,guided,failed", [("again", False, False), ("correct", True, False), ("correct", False, True)])
def test_actual_review_outcome_and_failure_marker_control_shared_settlement(vertical, outcome, guided, failed):
    db, _value, _accepted, module = vertical
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    assert issue(db, "physical-black", entry)["issued"]
    if failed: db.execute("UPDATE daily_queue SET attempt_failed=1 WHERE id=?", (entry,))
    assert complete(db, entry, outcome=outcome, guided=guided)["persisted"]
    assert len(module.receipts) == 1 and not module.settlements and module.outstanding


def test_target_burial_and_preexisting_issued_attempt_are_deferred_by_shared_admission(vertical):
    db, _value, _accepted, module = vertical
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    assert issue(db, "physical-black", entry)["issued"]
    assert bridge.admit_card(db, {"card_id": "physical-black"}) == dict(status="deferred", reason="issued_head_preserved", queue_entry_id=None)
    db.execute("UPDATE daily_queue SET status='buried' WHERE id=?", (entry,))
    assert bridge.admit_card(db, {"card_id": "physical-black"})["reason"] == "buried_today"
    assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='physical-black'").fetchone()[0] == 1


def test_reconciled_persisted_review_consumes_the_same_existing_binding(vertical):
    db, _value, _accepted, module = vertical
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    assert issue(db, "physical-black", entry)["issued"]
    complete(db, entry, outcome="again")
    assert module.outstanding
    result = complete(db, entry, attempt_id="offline-second", recorded_at=datetime.now(timezone.utc).isoformat())
    assert result["review_id"] is not None and result["reconciliation"] == "chronological"
    assert not module.outstanding and len(module.attempts) == len(module.settlements) == 1


def test_status_endpoint_is_read_only_and_separates_acceptance_queue_and_settlement(vertical, monkeypatch):
    db, value, _accepted, module = vertical
    from app import game_evidence_routes as routes
    monkeypatch.setattr(routes, "postgres_store", SimpleNamespace(configured=lambda: True))
    monkeypatch.setattr(routes, "read_connection", lambda: nullcontext(db))
    assert len([route for route in routes.router.routes if "GET" in route.methods]) == 1
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    before = db.sqlite.total_changes
    db.read_only = True
    status = routes.legacy_card_status("physical-black")
    assert status["target_id"] == value.authority.target_id and status["workspace_id"] == "legacy"
    assert status["accepted"] and status["current"] and status["settlement"] == "unsettled"
    assert status["played_move"] == "g8f6" and status["expected_moves"] == ["a7a6"]
    assert status["admission"]["queue_entry_id"] == entry and status["admission"]["waiting_behind_protected_head"]
    db.current = False
    stale = routes.legacy_card_status("physical-black")
    assert stale["accepted"] and not stale["current"] and stale["admission"]["queue_entry_id"] == entry
    assert db.sqlite.total_changes == before and not module.attempts and not module.receipts


def test_invalidation_requires_the_actual_review_owner_write(vertical):
    db, _value, _accepted, module = vertical
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET status='complete' WHERE card_id='protected'")
    assert issue(db, "physical-black", entry)["issued"]
    result = complete(db, entry)
    with pytest.raises(ValueError, match="actual review"):
        bridge.invalidate_review(db, "physical-black", result["review_id"], "unsupported claim")
    assert not module.outstanding


def test_old_legacy_review_clearing_cannot_remove_a_current_bridge_priority(vertical):
    db, _value, _accepted, module = vertical
    from app.services.real_game_feedback import clear_satisfied_miss_priority, MISS_REASON
    entry = bridge.admit_card(db, {"card_id": "physical-black"})["queue_entry_id"]
    db.execute("UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?", (MISS_REASON, entry))
    clear_satisfied_miss_priority(db, "physical-black")
    assert db.execute("SELECT gameplay_priority_reason FROM daily_queue WHERE id=?", (entry,)).fetchone()[0] == BRIDGE_MISS_REASON
    assert module.outstanding and not module.receipts


def test_local_http_commands_only_dispatch_original_immutable_payloads(vertical, monkeypatch):
    _db, value, _accepted, module = vertical
    from app import game_evidence_routes as routes
    monkeypatch.setattr(routes, "postgres_store", SimpleNamespace(configured=lambda: True))
    sent = []
    monkeypatch.setattr(routes, "dispatch_command", lambda name, payload, **options:
        sent.append((name, payload, options)) or {"state": "pending"})
    assert routes.receive_legacy_evidence(value, "delivery-operation") == {"state": "pending"}
    assert routes.queue_legacy_evidence_card("physical-black", "admission-operation") == {"state": "pending"}
    assert sent == [("game_evidence.receive_legacy", {"delivery": value.model_dump(mode="json")},
                     dict(idempotency_key="delivery-operation", background=True)),
                    ("game_evidence.admit_legacy", {"card_id": "physical-black"},
                     dict(idempotency_key="admission-operation"))]
    assert not module.attempts and not module.receipts


@pytest.mark.parametrize("method,path,work_class,status", [
    ("POST", "/api/game-evidence/legacy/deliveries", "background", 200),
    ("POST", "/api/game-evidence/legacy/cards/physical-black/queue", "foreground", 200),
    ("GET", "/api/game-evidence/legacy/cards/physical-black", "foreground", 200),
    ("POST", "/api/game-evidence/legacy/cards/physical-black/unimplemented", None, 503),
])
def test_http_cutover_matching_background_admission_and_query_only_guard_are_preserved(monkeypatch, method, path, work_class, status):
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from app import main, postgres_store
    entered = []
    @asynccontextmanager
    async def foreground():
        entered.append("foreground")
        yield
    def background():
        entered.append("background")
        return nullcontext()
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "async_foreground", foreground)
    monkeypatch.setattr(main.activity_gate, "background_request", background)
    request = Request(dict(type="http", method=method, path=path, raw_path=path.encode(), query_string=b"",
        scheme="http", headers=[], server=("test", 80), client=("test", 1), http_version="1.1"))
    async def next_handler(_request):
        assert database._query_only_request.get() == (method == "GET")
        return JSONResponse({"dispatched": False})
    response = asyncio.run(main.prioritize_foreground_requests(request, next_handler))
    assert response.status_code == status and entered == ([work_class] if work_class else [])
    assert not database._query_only_request.get()
