"""Refresh acceptance on the existing labelled disposable PostgreSQL fixture."""
from contextlib import contextmanager, nullcontext
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import uuid
import json

import httpx
import pytest

from app.games.feedback.ports import JobState
from app.games.feedback.refresh import refresh_job, TASK_KIND
from app.games.feedback.review import SyncConfiguration
from app.product_composition.game_review import configured_job
from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
from test_product_game_lapse_postgres import owned_postgres, case

NOW = datetime.fromisoformat("2026-10-30T12:34:56+00:00").timestamp()


@pytest.fixture
def stores(owned_postgres):
    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw
    workspace = "refresh-" + uuid.uuid4().hex
    review, games = PostgresReviewStore(connections), PostgresGamesStore(connections)
    def configure(provider="lichess", revision=1, start_at="2026-10-20T00:00:00Z"):
        config = SyncConfiguration.model_validate_json(json.dumps(dict(
            workspace_id=workspace, provider=provider, account="Learner", repertoire_ids=["white"],
            speeds=["rapid"], rated=True, interval_seconds=None, revision=revision,
            window=dict(start_at=start_at, end_at="2026-10-21T00:00:00Z", precision="second"))))
        review.configure(config, revision-1)
        return config
    def request(config, operation, now=NOW):
        return review.enqueue_refresh(workspace, config.provider, operation,
            lambda current, checkpoint: refresh_job(current, checkpoint, now, operation, configured_job))
    try:
        yield SimpleNamespace(review=review, games=games, connections=connections,
            transaction=owned_postgres, workspace=workspace, configure=configure, request=request)
    finally:
        # Release only this case's synthetic unfinished leases. The module's
        # labelled fixture retains evidence and removes its own container.
        with connections() as database:
            database.execute("""UPDATE product_feedback_jobs SET status='failed',lease_until=0,
                payload=jsonb_set(jsonb_set(payload,'{status}','\"failed\"'),'{lease_until}','0')
                WHERE payload->'spec'->>'workspace_id'=%s AND status IN ('queued','running','retryable')""", (workspace,))


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_native_refresh_replay_coalescing_exact_status_and_unchanged_schedule(stores, provider):
    config = stores.configure(provider)
    first, created = stores.request(config, "first")
    assert created and first.spec.window.end_at == "2026-10-30T12:34:56+00:00"
    replay, created = stores.request(config, "first", NOW+500)
    assert replay == first and not created
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda operation: stores.request(config, operation), ("second", "third")))
    assert all(job.spec.job_id == first.spec.job_id for job, _ in results)
    # Reconstruct the store: reads are aliases to this exact job, never last_job.
    restored = PostgresReviewStore(stores.connections)
    assert restored.refresh_job(stores.workspace, "second") == first
    with stores.connections() as database:
        assert database.execute("SELECT next_run_at FROM product_feedback_configuration WHERE workspace_id=%s", (stores.workspace,)).fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM product_feedback_jobs WHERE payload->'spec'->>'workspace_id'=%s", (stores.workspace,)).fetchone()[0] == 1


def test_native_refresh_checkpoint_ignores_unfinished_manual_and_other_revision(stores):
    config = stores.configure()
    forward = configured_job(config, "following:lichess:1:covered").model_copy(update={
        "window": config.window.model_copy(update={"end_at": "2026-10-27T00:00:00Z"})})
    stores.games.enqueue(forward)
    stores.games.save_job(JobState(spec=forward, status="complete", window_complete=True))
    newer = configured_job(config, "unrelated-manual").model_copy(update={
        "window": config.window.model_copy(update={"end_at": "2026-10-29T00:00:00Z"})})
    stores.games.enqueue(newer)
    stores.games.save_job(JobState(spec=newer, status="complete", window_complete=True))
    first, _ = stores.request(config, "checkpoint")
    assert first.spec.window.start_at == "2026-10-25T00:00:00+00:00"
    stores.games.save_job(first.model_copy(update={"status": "complete", "window_complete": True}))
    next_job, _ = stores.request(config, "after-refresh", NOW+60)
    assert next_job.spec.window.start_at == "2026-10-28T12:34:56+00:00"
    stores.games.save_job(next_job.model_copy(update={"status": "complete", "window_complete": True}))
    revised = stores.configure(revision=2)
    different, _ = stores.request(revised, "new-revision")
    assert different.spec.window.start_at == "2026-10-20T00:00:00+00:00"


def test_native_refresh_job_leases_serialize_manual_and_automatic_and_keep_backoff(stores):
    config = stores.configure()
    manual, _ = stores.request(config, "manual")
    automatic = configured_job(config, "following:lichess:1:auto")
    stores.games.enqueue(automatic)
    claimed = stores.games.claim_job(NOW, NOW+120)
    assert claimed.spec.job_id == automatic.job_id
    assert stores.games.claim_job(NOW, NOW+120, job_id=manual.spec.job_id) is None
    retry = claimed.model_copy(update={"status": "retryable", "lease_until": 0,
        "next_attempt_at": NOW+60, "error": "rate_limited"})
    stores.games.save_job(retry)
    assert stores.games.claim_job(NOW+59, NOW+180, job_id=manual.spec.job_id) is None
    stores.games.save_job(stores.games.load_job(automatic.job_id).model_copy(update={"status": "failed"}))
    assert stores.games.claim_job(NOW+60, NOW+180, job_id=manual.spec.job_id).spec.job_id == manual.spec.job_id
    assert stores.games.claim_job(NOW+60, NOW+180, job_id=manual.spec.job_id) is None


def test_native_refresh_command_job_task_and_receipt_rollback_together(owned_postgres, monkeypatch):
    from app import command_gateway as gateway
    from app.product_composition import game_review_commands as commands
    monkeypatch.setattr(gateway, "_handlers", {})
    monkeypatch.setattr(gateway, "_preparers", {})
    monkeypatch.setattr(gateway, "_writer_connection", lambda background: owned_postgres())
    commands.register_commands()
    config = dict(workspace_id="legacy", provider="lichess", account="Learner", repertoire_ids=["white"],
        speeds=["rapid"], rated=True, interval_seconds=None, revision=1,
        window=dict(start_at="2026-10-20T00:00:00Z",end_at="2026-10-21T00:00:00Z",precision="second"))
    gateway.execute_command("refresh-native-config", "product_games.configure", dict(configuration=config, expected_revision=0))
    payload = dict(provider="lichess", operation_id="refresh-native-command")
    result = gateway.execute_command(payload["operation_id"], "product_games.refresh", payload)
    assert result == gateway.execute_command(payload["operation_id"], "product_games.refresh", payload)
    with owned_postgres() as database:
        assert database.execute_native("SELECT COUNT(*) FROM background_tasks WHERE kind=%s AND deduplication_key=%s", (TASK_KIND, result["job_id"])).fetchone()[0] == 1
    original = gateway._handlers["product_games.refresh"]
    def fail_after_staging(database, payload):
        original(database, payload)
        raise __import__("fastapi").HTTPException(409, "synthetic_after_refresh_task")
    monkeypatch.setitem(gateway._handlers, "product_games.refresh", fail_after_staging)
    assert gateway.execute_command("refresh-native-rollback", "product_games.refresh",
        dict(provider="lichess", operation_id="refresh-native-rollback")) is None
    with owned_postgres() as database:
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_sync_requests WHERE operation_id='refresh-native-rollback'").fetchone()[0] == 0
        assert database.execute_native("SELECT state FROM operation_receipts WHERE operation_id='refresh-native-rollback'").fetchone()[0] == "failed"


def test_native_held_provider_does_not_hide_other_provider_work(stores):
    config = stores.configure()
    held, _ = stores.request(config, "held-account")
    stores.games.claim_job(NOW, NOW+120, job_id=held.spec.job_id)
    for index in range(9):
        stores.games.enqueue(configured_job(config, f"a-held-{index}"))
    other = stores.configure("chesscom")
    expected = configured_job(other, "z-independent-provider")
    stores.games.enqueue(expected)
    assert stores.games.claim_job(NOW, NOW+120).spec.job_id == expected.job_id


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
@pytest.mark.parametrize("scenario", ["empty", "fresh-game-paginated", "rate-limited"])
def test_native_one_off_fetch_runs_with_collector_off_and_preserves_study(stores, case, monkeypatch, provider, scenario):
    from app.product_composition import game_refresh, game_sync
    from app.services import study_priority, redis_admission_gate, durable_tasks
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.games import product_review
    from app.product_composition.game_review import SystemClock
    monkeypatch.setattr(SystemClock, "now", lambda _: NOW)
    empty = scenario == "empty"
    config = stores.configure(provider, start_at="2026-09-20T00:00:00Z")
    job, _ = stores.request(config, "one-off")
    # Stage/claim using the existing task owner; no Celery process or live DSN.
    with stores.transaction() as database:
        task = durable_tasks.enqueue_task_in_transaction(database, TASK_KIND, job.spec.job_id,
            dict(workspace_id=stores.workspace, job_id=job.spec.job_id), priority=80)
        database.execute_native("UPDATE background_tasks SET state='leased',lease_token='native-refresh' WHERE id=%s", (task["id"],))
        case.service(database).accept_lapse(case.lapse)
        before = database.execute_native("""SELECT (SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM cards c),
            (SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM reviews r),
            (SELECT jsonb_agg(to_jsonb(q) ORDER BY id) FROM daily_queue q),
            (SELECT jsonb_agg(to_jsonb(f) ORDER BY effect_key) FROM study_game_feedback f)""").fetchone()
    task = {**task, "lease_token": "native-refresh", "payload": dict(workspace_id=stores.workspace, job_id=job.spec.job_id)}
    monkeypatch.delenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", raising=False)
    monkeypatch.setenv("TEMPO_DATABASE_WRITE_URL", "fixture-only")
    monkeypatch.setattr(product_review, "DedicatedGamesConnections", lambda _: stores.connections)
    monkeypatch.setattr(postgres_store, "connection", lambda **_: stores.transaction())
    monkeypatch.setattr(redis_admission_gate, "background_lease", nullcontext)
    monkeypatch.setattr(study_priority, "control_snapshot", lambda: dict(available=True, active=False, study_only_mode=False))
    calls = []
    pgn = '[Event "Synthetic"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'
    game_id = "refresh" + stores.workspace[-12:]
    def http(request):
        calls.append(str(request.url))
        if scenario == "rate-limited":
            return httpx.Response(429, headers={"Retry-After": "120"})
        if provider == "lichess":
            rows = [] if empty or int(request.url.params["since"]) > int((NOW-3600)*1000) else [dict(
                id=game_id, createdAt=int((NOW-3600)*1000), speed="rapid", rated=True, pgn=pgn)]
            return httpx.Response(200, text="\n".join(json.dumps(row) for row in rows), headers={"Content-Type": "application/x-ndjson"})
        if request.url.path.endswith("/archives"):
            return httpx.Response(200, json={"archives": [] if empty else [
                "https://api.chess.com/pub/player/learner/games/2026/09",
                "https://api.chess.com/pub/player/learner/games/2026/10"]})
        games = [] if request.url.path.endswith("/09") else [dict(uuid=game_id, url="https://www.chess.com/game/live/123",
            end_time=int(NOW-3600), time_class="rapid", rated=True, rules="chess", pgn=pgn)]
        return httpx.Response(200, json={"games": games})
    real_factory = game_sync.create_game_sync
    def runtime(**options):
        options["policies"] = tuple(policy.model_copy(update={"page_size": 1}) for policy in options["policies"])
        return real_factory(**options, transport_factory=lambda: httpx.MockTransport(http))
    monkeypatch.setattr(game_sync, "create_game_sync", runtime)
    for _ in range(4):
        more = game_refresh.execute_refresh_slice(task)
        if not more or scenario == "rate-limited":
            break
        with stores.transaction() as database:
            database.execute_native("UPDATE background_tasks SET state='leased',lease_token='native-refresh' WHERE id=%s", (task["id"],))
    saved = stores.games.load_job(job.spec.job_id)
    if scenario == "rate-limited":
        assert saved.status == "retryable" and saved.error == f"{provider}_rate_limited"
        assert saved.next_attempt_at >= NOW + 120
        with stores.transaction() as database:
            eligible = database.execute_native("SELECT next_attempt_at FROM background_tasks WHERE id=%s", (task["id"],)).fetchone()[0]
            assert datetime.fromisoformat(eligible).timestamp() >= NOW + 120
    else:
        assert saved.status == "complete", saved
    if scenario == "fresh-game-paginated":
        assert saved.pages_committed >= 2
        with stores.connections() as database:
            assert database.execute("SELECT COUNT(*) FROM imported_games WHERE provider_game_id=%s", (game_id,)).fetchone()[0] == 1
    assert calls
    with stores.transaction() as database:
        after = database.execute_native("""SELECT (SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM cards c),
            (SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM reviews r),
            (SELECT jsonb_agg(to_jsonb(q) ORDER BY id) FROM daily_queue q),
            (SELECT jsonb_agg(to_jsonb(f) ORDER BY effect_key) FROM study_game_feedback f)""").fetchone()
        assert tuple(after) == tuple(before)
        assert database.execute_native("SELECT next_run_at FROM product_feedback_configuration WHERE workspace_id=%s", (stores.workspace,)).fetchone()[0] == 0
    monkeypatch.setattr(study_priority, "control_snapshot", lambda: dict(available=True, active=False, study_only_mode=True))
    with pytest.raises(redis_admission_gate.BackgroundAdmissionDeferred):
        game_refresh.execute_refresh_slice(task)
