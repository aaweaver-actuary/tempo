"""Fake-only deletion storage boundaries without Main, SQLite or service startup."""

from datetime import date
import json
from types import ModuleType, SimpleNamespace
import sys

import chess
import pytest

from app import card_deletion as application


class Rows:
    def __init__(self, value=None, rows=(), events=None):
        self.value, self.rows, self.events = value, rows, events

    def fetchone(self):
        if self.events is not None:
            self.events.append("fetchone")
        return self.value

    def fetchall(self):
        if self.events is not None:
            self.events.append("fetchall")
        return list(self.rows)

    def __iter__(self):
        if self.events is not None:
            self.events.append("iterate")
        return iter(self.rows)


EXPECTED_SQL = {
    'UPDATE repertoires SET is_main=1 WHERE id=%s',
    'DELETE FROM card_revisions WHERE card_id=ANY(%s)',
    'DELETE FROM cards WHERE id=ANY(%s)',
    'DELETE FROM daily_queue WHERE card_id=ANY(%s)',
    'DELETE FROM endgame_templates WHERE card_id=ANY(%s)',
    'DELETE FROM opening_evidence_attempts WHERE card_id=ANY(%s)',
    'DELETE FROM opening_evidence_presentations WHERE card_id=ANY(%s)',
    'DELETE FROM opening_evidence_queue_contexts WHERE queue_entry_id=ANY(%s) OR presentation_snapshot_id IN (SELECT id FROM opening_evidence_presentations WHERE card_id=ANY(%s))',
    'DELETE FROM opening_graph_steps WHERE card_id=ANY(%s)',
    'DELETE FROM queue_projection_diagnostics WHERE card_id=ANY(%s)',
    'DELETE FROM repertoire_comparisons_legacy WHERE repertoire_id=%s',
    'DELETE FROM repertoire_comparisons_staged WHERE repertoire_id=%s',
    'DELETE FROM repertoires WHERE id=%s',
    'DELETE FROM study_attempts WHERE card_id=ANY(%s)',
    'DELETE FROM tactic_progress WHERE card_id=ANY(%s)',
    'INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1) ON CONFLICT(repertoire_id,card_id) DO NOTHING',
    "INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,'Retained cards','Cards kept after repertoire deletion',%s,0) ON CONFLICT(id) DO NOTHING",
    'SELECT DISTINCT observation.decision_id FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt ON attempt.attempt_id=observation.attempt_id WHERE attempt.card_id=ANY(%s)',
    'SELECT DISTINCT repertoire_id,generation,line_id FROM opening_graph_steps WHERE card_id=%s ORDER BY repertoire_id,generation,line_id',
    'SELECT card.id,card.start_fen,card.moves_json,COALESCE(card.trained_color,(SELECT line.trained_color FROM repertoire_lines line WHERE line.repertoire_id=card.repertoire_id ORDER BY line.created_at,line.id LIMIT 1)) AS effective_trained_color,card.introduced_at,card.first_correct_at,EXISTS(SELECT 1 FROM reviews WHERE card_id=card.id) AS has_reviews,(SELECT MIN(link.repertoire_id) FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id<>%s) AS replacement FROM cards card WHERE card.repertoire_id=%s ORDER BY card.id FOR UPDATE OF card',
    'SELECT decision_id,observation_json FROM opening_evidence_observations WHERE decision_id=ANY(%s)',
    'SELECT decision_index,card_id,parent_card_id FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s AND line_id=%s ORDER BY decision_index',
    "SELECT deduplication_key,payload_json FROM background_tasks WHERE id=%s AND state IN ('queued','leased','retrying') FOR UPDATE",
    "SELECT id FROM cards WHERE repertoire_id=%s UNION SELECT card_id FROM daily_queue WHERE admission_repertoire_id=%s AND status='queued' ORDER BY 1",
    'SELECT id FROM daily_queue WHERE card_id IN (%s)',
    'SELECT id FROM repertoires WHERE id NOT IN (%s,%s,%s,%s,%s,%s,%s) ORDER BY created_at DESC,id LIMIT 1',
    "SELECT id,deduplication_key,payload_json FROM background_tasks WHERE state IN ('queued','leased','retrying') AND (deduplication_key=%s OR position(%s in payload_json)>0) ORDER BY id",
    'SELECT id,is_main FROM repertoires WHERE id=%s FOR UPDATE',
    'SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
    'SELECT revision FROM cards WHERE id=%s FOR UPDATE',
    "SELECT source_ref FROM cards WHERE id IN (%s) AND content_type='tactic' AND source_ref IS NOT NULL",
    "UPDATE background_tasks SET state='complete',phase='cancelled',lease_token=NULL,lease_expires_at=NULL,completed_at=%s,updated_at=%s WHERE id=%s",
    'UPDATE cards SET repertoire_id=%s,pending_validation=CASE WHEN %s=%s THEN 0 ELSE pending_validation END WHERE id=%s',
    'UPDATE cards SET trained_color=%s WHERE id=%s AND trained_color IS NULL',
    "UPDATE daily_queue SET admission_repertoire_id=(\n        SELECT card.repertoire_id FROM cards card WHERE card.id=daily_queue.card_id\n    ) WHERE admission_repertoire_id=%s AND status='queued'\n      AND EXISTS(SELECT 1 FROM cards card JOIN repertoires owner ON owner.id=card.repertoire_id\n                 WHERE card.id=daily_queue.card_id AND owner.id<>%s)",
    'UPDATE game_findings SET card_id=NULL WHERE card_id IN (%s)',
    'UPDATE game_findings SET repertoire_id=NULL WHERE repertoire_id=%s',
    'UPDATE threat_training_candidates SET paused_at=%s WHERE card_id IN (%s)',
}


class Compatible:
    def __init__(self, events, replies=()):
        self.events, self.replies = events, iter(replies)
        self.calls, self.batches = [], []

    @property
    def execute(self):
        self.events.append("execute_lookup")
        return self._execute

    def _execute(self, statement, parameters=()):
        assert statement in EXPECTED_SQL, f"Unexpected SQL: {statement}"
        self.calls.append((statement, parameters))
        self.events.append(("execute", len(self.calls), parameters))
        return next(self.replies, Rows())

    def executemany(self, statement, parameters):
        assert statement in {
            "INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s) ON CONFLICT(card_id) DO NOTHING",
            "UPDATE opening_graph_steps SET parent_card_id=%s WHERE repertoire_id=%s AND generation=%s AND line_id=%s AND decision_index=%s",
        }, f"Unexpected SQL: {statement}"
        self.batches.append((statement, parameters))
        self.events.append(("many", parameters))

    def execute_native(self, statement, parameters=()):
        return self._execute(statement, parameters)

    def __getattr__(self, name):
        raise AssertionError("Unexpected transaction/lifecycle access: " + name)


class Native(Compatible):
    pass


def module(monkeypatch, name, **attributes):
    value = ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    monkeypatch.setitem(sys.modules, name, value)


def clocks(monkeypatch, events):
    def now(zone):
        events.append("clock")
        return SimpleNamespace(isoformat=lambda: "timestamp")

    monkeypatch.setattr(application, "datetime", SimpleNamespace(now=now))
    monkeypatch.setattr(
        application,
        "capture_study_calendar",
        lambda db: SimpleNamespace(day=date(2026, 10, 20)),
    )


@pytest.mark.parametrize("native", [False, True])
def test_cancellation_queries_then_clocks_fetches_and_rechecks_only_exact_matches(
    monkeypatch, native
):
    events = []
    tasks = [
        dict(id="one", deduplication_key="old", payload_json="null"),
        dict(
            id="two", deduplication_key="other", payload_json='{"repertoire_id":"old"}'
        ),
        dict(
            id="three",
            deduplication_key="other",
            payload_json=json.dumps(
                {"nested": {"repertoire_id": "old"}, "fen": "old\0position"}
            ),
        ),
    ]
    replies = [Rows(rows=tasks, events=events)]
    replies += [
            Rows(dict(deduplication_key="old", payload_json="null"), events=events),
            Rows(),
            Rows(dict(deduplication_key="other", payload_json="null"), events=events),
        ]
    database = (Native if native else Compatible)(events, replies)
    clocks(monkeypatch, events)
    application.cancel_repertoire_tasks(database, "old")
    assert events.index("clock") < events.index("fetchall")
    assert events.index(("execute", 1, ("old", "old"))) < events.index("clock")
    parameters = [item[1] for item in database.calls]
    assert parameters == [("old", "old"), ("one",), ("timestamp", "timestamp", "one"), ("two",)]
    assert events.count("clock") == 1


def test_purge_preserves_identifier_reads_clock_points_fixed_targets_and_tombstones(
    monkeypatch,
):
    events = []
    database = Compatible(
        events, [Rows(rows=[], events=events), Rows(rows=[], events=events)]
    )
    clocks(monkeypatch, events)
    application.purge_card_data(database, ["card"])
    assert [item[1] for item in database.calls] == [
        ["card"], ["card"], (["card"],), (["card"],), ([], ["card"]), (["card"],), ([],),
        ["card"], ("timestamp", "card"), *([(["card"],)] * 7),
    ]
    assert events[:6] == [
        "execute_lookup",
        ("execute", 1, ["card"]),
        "iterate",
        "execute_lookup",
        ("execute", 2, ["card"]),
        "iterate",
    ]
    first_clock = events.index("clock")
    assert events[first_clock - 1] == "execute_lookup"
    assert events[first_clock + 1] == ("execute", 9, ("timestamp", "card"))
    second_clock = events.index("clock", first_clock + 1)
    assert events[second_clock + 1] == ("many", [("card", "timestamp")])
    assert database.batches[0][1] == [("card", "timestamp")]
    assert events[-1] == ("execute", 16, (["card"],))


@pytest.mark.parametrize("policy", ["keep", "delete"])
def test_repertoire_delete_keeps_shared_owner_retention_policy_and_refresh_after_writes(
    monkeypatch, policy
):
    events = []
    owned = [
        dict(
            id="learned",
            replacement=None,
            introduced_at="day",
            first_correct_at=None,
            has_reviews=True,
            effective_trained_color=None,
            start_fen=chess.STARTING_FEN,
            moves_json='["e2e4"]',
        ),
        dict(
            id="unlearned",
            replacement=None,
            introduced_at=None,
            first_correct_at=None,
            has_reviews=False,
        ),
        dict(id="shared", replacement="other"),
    ]
    replies = [Rows(), Rows(dict(id="old", is_main=1)), Rows(), Rows(rows=owned, events=events)]
    replies += [Rows()] * (10 if policy == "keep" else 6)
    replies += [Rows(("replacement-main",)), Rows()]
    database = Compatible(events, replies)
    clocks(monkeypatch, events)
    monkeypatch.setattr(
        application, "cancel_repertoire_tasks", lambda *args: events.append("cancel")
    )
    monkeypatch.setattr(
        application,
        "purge_card_data",
        lambda connection, identifiers: events.append(("purge", identifiers)),
    )
    module(
        monkeypatch,
        "app.queue_commands",
        request_queue_refresh_in_transaction=lambda *args: events.append(
            ("queue", args)
        ),
    )
    module(
        monkeypatch,
        "app.services.durable_tasks",
        enqueue_task_in_transaction=lambda *args, **keywords: events.append(
            ("task", args, keywords)
        ),
    )
    result = application.delete_repertoire_data(database, "old", policy)
    assert events[:2] == [("execute", 1, ("tempo:main-repertoire",)), "cancel"]
    assert (
        "purge",
        ["unlearned"] if policy == "keep" else ["learned", "unlearned"],
    ) in events
    parameters = [item[1] for item in database.calls]
    if policy == "keep":
        retained = application.RETAINED_REPERTOIRE_ID
        assert parameters[4:8] == [
            (retained, "timestamp"),
            (retained, "learned"),
            ("white", "learned"),
            (retained, retained, retained, "learned"),
        ]
        clock = events.index("clock")
        assert events[clock - 1] == "execute_lookup"
        assert events[clock + 1] == ("execute", 5, (retained, "timestamp"))
    assert (
        "other",
        "other",
        application.RETAINED_REPERTOIRE_ID,
        "shared",
    ) in parameters
    assert parameters[-2:] == [application.SYSTEM_REPERTOIRE_IDS, ("replacement-main",)]
    assert events[-1:] == [
        ("queue", (database, "2026-10-20")),
    ]
    assert result == dict(deleted=True, id="old")


def test_permanent_delete_reparents_occurrences_before_graph_purge_and_refresh(
    monkeypatch,
):
    class Route(dict):
        def __iter__(self):
            return iter(self.values())

    events = []
    route = Route(repertoire_id="rep", generation=4, line_id="line")
    dependencies = [
        (0, "ancestor", None),
        (1, "deleted", "ancestor"),
        (2, "child", "deleted"),
        (3, "deleted", "child"),
        (4, "tail", "deleted"),
    ]
    database = Native(
        events,
        [
            Rows(),
            Rows(dict(revision=2)),
            Rows(rows=[route]),
            Rows(
                rows=[
                    dict(decision_index=index, card_id=card, parent_card_id=parent)
                    for index, card, parent in dependencies
                ],
                events=events,
            ),
            Rows(),
        ],
    )
    clocks(monkeypatch, events)
    monkeypatch.setattr(
        application,
        "purge_card_data",
        lambda connection, identifiers: events.append(("purge", identifiers)),
    )
    module(
        monkeypatch,
        "app.queue_commands",
        request_queue_refresh_in_transaction=lambda *args: events.append(
            ("queue", args)
        ),
    )
    assert application.permanent_delete_card(database, "deleted", 2) == dict(
        deleted=True, card_id="deleted"
    )
    assert database.calls[0][1] == ("tempo:card-edit:deleted",)
    assert database.calls[3][1] == ("rep", 4, "line")
    assert database.batches[0][1] == [
        ("ancestor", "rep", 4, "line", 2),
        ("child", "rep", 4, "line", 4),
    ]
    assert events.index(("many", database.batches[0][1])) < events.index(
        ("execute", 5, (["deleted"],))
    )
    assert events[-2:] == [
        ("purge", ["deleted"]),
        ("queue", (database, "2026-10-20")),
    ]


def test_permanent_delete_stale_revision_stops_after_lock_and_read():
    events = []
    database = Native(events, [Rows(), Rows(dict(revision=3))])
    with pytest.raises(application.HTTPException) as error:
        application.permanent_delete_card(database, "card", 2)
    assert error.value.status_code == 409
    assert len(database.calls) == 2 and database.batches == []


@pytest.mark.parametrize("attribute", ["connect", "commit", "rollback", "close"])
def test_borrowed_connection_rejects_transaction_and_lifecycle_ownership(attribute):
    database = Compatible([])
    with pytest.raises(AssertionError, match="Unexpected transaction/lifecycle access"):
        getattr(database, attribute)()


def test_native_fake_rejects_unknown_sql_and_wrong_cancellation_predicate():
    database = Compatible([])
    for statement in (
        "SELECT * FROM unexpected_table",
        "SELECT id,deduplication_key,payload_json FROM background_tasks WHERE state IN ('queued','leased','retrying') AND (deduplication_key=%s AND position(%s in payload_json)>0) ORDER BY id",
    ):
        with pytest.raises(AssertionError, match="Unexpected SQL"):
            database.execute_native(statement, ("old", "old"))
    assert database.calls == []
