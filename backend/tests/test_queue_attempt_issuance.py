"""PR142: admission identity must not authorize an unissued later attempt."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import database, main


START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def queued_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "issuance.db")
    database.initialize()
    entries = {}
    with database.connection() as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('issuance','Issuance','synthetic',?)", (date.today().isoformat(),))
        for position, card_id in enumerate(("active-a", "later-b")):
            connection.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type)
                   VALUES(?,'issuance','prefix',?,'["e2e4"]','learning',?,'tactic')""",
                (card_id, START_FEN, date.today().isoformat()),
            )
            entries[card_id] = connection.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
                (date.today().isoformat(), card_id, position),
            ).lastrowid
    return entries


def _business_snapshot():
    with database.read_connection() as connection:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("cards", "daily_queue", "reviews")
        }


@pytest.mark.parametrize("action", ["fail", "bury"])
def test_identified_never_issued_non_head_attempt_is_rejected(queued_attempts, action):
    client = TestClient(main.app)
    issued = client.get("/api/queue/today")
    assert issued.status_code == 200, issued.text
    assert [card["id"] for card in issued.json()["cards"]] == ["active-a", "later-b"]
    before = _business_snapshot()
    rejected = client.post(
        f"/api/queue/entries/{queued_attempts['later-b']}/{action}",
        json={"card_id": "later-b", "expected_revision": 1},
    )
    assert rejected.status_code == 409, rejected.text
    assert _business_snapshot() == before


def _promote_real_game_miss():
    from datetime import datetime, timezone
    from test_real_game_feedback import _seed_game, _seed_repertoire
    from app.services.repertoire_comparison import compare_games
    from app.services.real_game_feedback import apply_real_game_misses

    with database.connection() as connection:
        _seed_repertoire(connection, studied_at='2026-01-01T00:00:00Z')
        _seed_game(connection, 'issuance-miss', ['d2d4'], datetime.now(timezone.utc).isoformat())
    compare_games(['issuance-miss'])
    apply_real_game_misses('issuance-miss')
    with database.read_connection() as connection:
        assert [row[0] for row in connection.execute(
            "SELECT card_id FROM daily_queue WHERE status='queued' ORDER BY position,id"
        )] == ['card', 'active-a', 'later-b']


@pytest.mark.parametrize('action', ['fail', 'bury'])
def test_identified_current_head_attempt_remains_operable(queued_attempts, action):
    client = TestClient(main.app)
    assert client.get('/api/queue/today').status_code == 200
    result = client.post(f"/api/queue/entries/{queued_attempts['active-a']}/{action}",
                         json={'card_id': 'active-a', 'expected_revision': 1})
    assert result.status_code == 200, result.text


@pytest.mark.parametrize('action', ['fail', 'bury'])
def test_issued_attempt_survives_real_game_miss_promotion(queued_attempts, action):
    client = TestClient(main.app)
    assert client.get('/api/queue/window?limit=2').status_code == 200
    _promote_real_game_miss()
    database.initialize()  # Reopen/reinitialize without manufacturing another issuance.
    before = _business_snapshot()
    rejected = client.post(f"/api/queue/entries/{queued_attempts['later-b']}/{action}",
                           json={'card_id': 'later-b', 'expected_revision': 1})
    assert rejected.status_code == 409, rejected.text
    assert _business_snapshot() == before
    result = client.post(f"/api/queue/entries/{queued_attempts['active-a']}/{action}",
                         json={'card_id': 'active-a', 'expected_revision': 1})
    assert result.status_code == 200, result.text
    with database.read_connection() as connection:
        assert [tuple(row) for row in connection.execute(
            'SELECT card_id,issued_as_head FROM queue_attempt_origins ORDER BY queue_entry_id'
        )] == [('active-a', 1), ('later-b', 0), ('card', 0)]


@pytest.mark.parametrize('action', ['fail', 'bury'])
@pytest.mark.parametrize('identity', [None, {'card_id': 'active-a'}, {'expected_revision': 1}])
def test_legacy_queue_commands_remain_head_only(queued_attempts, action, identity):
    client = TestClient(main.app)
    assert client.get('/api/queue/today').status_code == 200
    later_identity = {**identity, 'card_id': 'later-b'} if identity and 'card_id' in identity else identity
    assert client.post(f"/api/queue/entries/{queued_attempts['later-b']}/{action}", json=later_identity).status_code == 409
    assert client.post(f"/api/queue/entries/{queued_attempts['active-a']}/{action}", json=identity).status_code == 200


@pytest.mark.parametrize('path', ['/api/queue/today', '/api/queue/prepared', '/api/queue/window?limit=2'])
def test_queue_response_issues_only_head_without_business_mutation(queued_attempts, path):
    before = _business_snapshot()
    client = TestClient(main.app)
    for _ in range(2):
        issued = client.get(path)
        assert issued.status_code == 200, issued.text
        assert [card['id'] for card in issued.json()['cards']] == ['active-a', 'later-b']
        assert _business_snapshot() == before
    with database.read_connection() as connection:
        assert [tuple(row) for row in connection.execute(
            'SELECT card_id,issued_as_head FROM queue_attempt_origins ORDER BY queue_entry_id'
        )] == [('active-a', 1), ('later-b', 0)]


@pytest.mark.parametrize('action', ['fail', 'bury'])
@pytest.mark.parametrize('replacement', ['revision', 'card'])
def test_issued_marker_cannot_transfer_to_replacement_identity(queued_attempts, action, replacement):
    client = TestClient(main.app)
    assert client.get('/api/queue/today').status_code == 200
    _promote_real_game_miss()
    with database.connection() as connection:
        if replacement == 'revision':
            connection.execute("UPDATE cards SET revision=2,moves_json='[\"d2d4\"]' WHERE id='active-a'")
            identity = {'card_id': 'active-a', 'expected_revision': 2}
        else:
            connection.execute("UPDATE daily_queue SET card_id='later-b',cycle=1 WHERE id=?", (queued_attempts['active-a'],))
            identity = {'card_id': 'later-b', 'expected_revision': 1}
    before = _business_snapshot()
    rejected = client.post(f"/api/queue/entries/{queued_attempts['active-a']}/{action}", json=identity)
    assert rejected.status_code == 409, rejected.text
    assert _business_snapshot() == before


def test_queue_issuance_rejects_promotion_between_response_read_and_commit(queued_attempts, monkeypatch):
    from app import queue_commands
    original_issue = queue_commands.issue_queue_head
    def promote_before_issuance(connection, payload):
        connection.execute("UPDATE daily_queue SET position=-1 WHERE card_id='later-b'")
        return original_issue(connection, payload)
    monkeypatch.setattr(queue_commands, 'issue_queue_head', promote_before_issuance)
    response = TestClient(main.app).get('/api/queue/today')
    assert response.status_code == 503, response.text
    with database.read_connection() as connection:
        assert connection.execute('SELECT SUM(issued_as_head) FROM queue_attempt_origins').fetchone()[0] == 0


def test_postgres_queue_issuance_uses_worker_and_never_returns_unconfirmed_head(queued_attempts, monkeypatch):
    from app import command_dispatch
    from fastapi.responses import JSONResponse
    calls = []
    def pending_issuance(command, payload, **options):
        calls.append((command, payload, options))
        return JSONResponse(status_code=202, content={'state': 'queued'})
    from contextlib import contextmanager
    import sqlite3
    @contextmanager
    def sqlite_reader():
        connection = sqlite3.connect(database.DB_PATH)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
    monkeypatch.setattr(main, 'read_connection', sqlite_reader)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', pending_issuance)
    with pytest.raises(main.HTTPException) as failure:
        main._issue_returned_queue_head(date.today().isoformat(),
            {'id': 'active-a', 'queue_entry_id': queued_attempts['active-a'], 'revision': 1})
    assert failure.value.status_code == 503
    assert calls == [('queue.issue_head', {'queue_date': date.today().isoformat(),
        'entry_id': queued_attempts['active-a'], 'card_id': 'active-a', 'expected_revision': 1},
        {'idempotency_key': None})]


def test_queue_issuance_upgrade_does_not_infer_historical_heads(queued_attempts):
    with database.connection() as connection:
        connection.execute('ALTER TABLE queue_attempt_origins DROP COLUMN issued_as_head')
    database.initialize()
    database.initialize()
    with database.read_connection() as connection:
        assert [row[0] for row in connection.execute(
            'SELECT issued_as_head FROM queue_attempt_origins ORDER BY queue_entry_id'
        )] == [0, 0]


def test_sqlite_issuance_upgrade_rolls_back_column_and_trigger_changes(queued_attempts):
    from app.queue_attempt_origins import initialize_sqlite_origins

    with database.connection() as connection:
        connection.execute('ALTER TABLE queue_attempt_origins DROP COLUMN issued_as_head')
        connection.commit()
        original_triggers = connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'queue_attempt_origin_%' ORDER BY name"
        ).fetchall()

        class InterruptedSchemaUpgrade:
            def execute(self, statement, *parameters):
                result = connection.execute(statement, *parameters)
                if statement.startswith('DROP TRIGGER IF EXISTS queue_attempt_origin_insert'):
                    raise RuntimeError('Interrupted schema upgrade')
                return result

        with pytest.raises(RuntimeError, match='Interrupted schema upgrade'):
            initialize_sqlite_origins(InterruptedSchemaUpgrade())
        assert 'issued_as_head' not in {
            row[1] for row in connection.execute('PRAGMA table_info(queue_attempt_origins)')
        }
        assert connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'queue_attempt_origin_%' ORDER BY name"
        ).fetchall() == original_triggers


def test_queue_issuance_is_only_queue_get_write_exception():
    import asyncio
    from starlette.requests import Request
    from starlette.responses import Response
    async def probe():
        observed = {}
        for path in ('/api/queue/today', '/api/queue/prepared', '/api/queue/window',
                     '/api/queue/entries/1/state', '/api/settings'):
            request = Request({'type': 'http', 'method': 'GET', 'path': path,
                               'headers': [], 'query_string': b'', 'scheme': 'http',
                               'server': ('test', 80)})
            async def downstream(_request):
                observed[path] = database._query_only_request.get()
                return Response(status_code=204)
            await main.prioritize_foreground_requests(request, downstream)
        return observed
    assert asyncio.run(probe()) == {
        '/api/queue/today': False, '/api/queue/prepared': False, '/api/queue/window': False,
        '/api/queue/entries/1/state': True, '/api/settings': True,
    }
