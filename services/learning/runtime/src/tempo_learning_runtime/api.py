"""Learning-only HTTP composition. Authentication precedes database access."""

import asyncio, hmac, json, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit
import chess
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from tempo_learning_database.adapters.api import psycopg, PoolTimeout
from .config import Configuration
from tempo_learning_database.connections import Database, connection
from . import gateway, queue_commands, queue_application, study_attempt_commands
from .clock import date
from tempo_learning_database.adapters import api as storage
from .models import (
    ReviewRequest,
    QueueAttemptFailureRequest,
    TacticAttemptRequest,
    EndgameProbeRequest,
    DefenseAttemptRequest,
    DefenseRecognitionRequest,
)
from .study_contracts import StudyAttemptRequest, StudySelfAssessmentRequest
from .review_adapter import submit_legacy_review
from .review_conflicts import ReviewConflict
from . import opening_evidence as evidence
from .opening_evidence_contracts import OpeningEvidenceCheckpoint
from .maintenance import safe_run_once
from .publications import PublicationBundle, stage, activate, retire
from .tactic_commands import submit_tactic_attempt
from .puzzles import validate_puzzle_record
from .defense import (
    read_defense_exercise,
    submit_defense_attempt,
    submit_defense_recognition,
)
from tempo_learning_database.adapters import endgame_store
from .receivers import receive
from .receivers import EvidenceDelivery
from . import study_bootstrap, teaching_commands
from .host_activity import HostActivity
from . import publication_bridge


def reconcile(database, payload):
    storage.begin_reconciliation(database)
    try:
        result = submit_legacy_review(database, payload)
    except ReviewConflict as error:
        storage.rollback_reconciliation(database)
        result = {"persisted": False, "conflict": error.information()}
    storage.release_reconciliation(database)
    return result


def save_endgame_attempt(db, payload):
    template = storage.active_endgame_template(db, payload["template_id"])
    if template is None:
        raise HTTPException(409, "Endgame template unavailable")
    card = storage.lock_endgame_card(db, template["card_id"])
    # Retirement acquires this same card lock before disabling the template.
    current = storage.endgame_enabled(db, payload["template_id"])
    if not card or not card[0] or not current[0]:
        raise HTTPException(409, "Endgame definition retired")
    attempt_id = uuid.uuid4().hex
    storage.insert_endgame_attempt(db, attempt_id, payload["template_id"], payload["fen"], payload["target"], datetime.now(timezone.utc).isoformat())
    return {
        "id": attempt_id,
        "fen": payload["fen"],
        "target": payload["target"],
        "moves": payload["moves"],
    }


def register_handlers():
    if "queue.issue_head" not in gateway._handlers:
        queue_commands.register_handlers()
    if "studies.attempts.submit" not in gateway._handlers:
        study_attempt_commands.register_handlers()
    handlers = {
        "study.settings.update": study_bootstrap.update_settings,
        "cards.teaching.record": teaching_commands.record_teaching,
        "cards.review": submit_legacy_review,
        "cards.review.reconcile": reconcile,
        "tactics.attempt.submit": submit_tactic_attempt,
        "defense.attempt.submit": lambda db, p: submit_defense_attempt(
            p["candidate_id"],
            write_database=db,
            **p["request"],
            light_first_interval_days=storage.light_first_interval_days(db),
        ),
        "defense.recognition.submit": lambda db, p: submit_defense_recognition(
            p["candidate_id"],
            DefenseRecognitionRequest.model_validate(p["request"]),
            write_database=db,
        ),
        "opening_evidence.checkpoint": lambda db, p: evidence.persist_checkpoint(db, p),
        "endgames.attempt.create": save_endgame_attempt,
        "publication.activate": activate,
        "publication.activate.v2": publication_bridge.activate,
        "publication.retire": retire,
    }
    for name, handler in handlers.items():
        if name not in gateway._handlers:
            gateway.register_command(name, handler)


def create_app(configuration: Configuration):
    register_handlers()
    database = Database(configuration)
    activity = HostActivity(configuration.host_activity_directory, source=configuration.workspace_id)

    @asynccontextmanager
    async def lifespan(app):
        stopping = asyncio.Event()
        activity.start()

        async def maintain():
            while not stopping.is_set():
                with database.scope():
                    await asyncio.to_thread(safe_run_once)
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(maintain()) if configuration.maintenance else None
        try:
            yield
        finally:
            stopping.set()
            if task:
                await task
            try:
                activity.close()
            finally:
                database.close()

    app = FastAPI(title="Tempo Learning", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.host_activity = activity

    @asynccontextmanager
    async def request_activity():
        # Host activity updates memory only; publication has its own daemon.
        with activity.request():
            yield

    @app.middleware("http")
    async def authority(request: Request, call_next):
        if (
            request.url.path not in ("/health/live", "/health/ready")
            and request.method != "OPTIONS"
        ):
            authorization = request.headers.get("authorization", "")
            credential = (
                configuration.operator_token
                if request.url.path.startswith("/internal/publications/")
                else configuration.evidence_token
                if request.url.path == "/internal/evidence"
                else configuration.token
            )
            if credential is None or not hmac.compare_digest(
                authorization, "Bearer " + credential
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Learning workspace credential required"},
                )
            if (
                request.headers.get("x-tempo-workspace", configuration.workspace_id)
                != configuration.workspace_id
            ):
                return JSONResponse(
                    status_code=403, content={"detail": "Workspace scope mismatch"}
                )
            origin = request.headers.get("origin")
            if origin is None and request.headers.get("sec-fetch-site"):
                referer = urlsplit(request.headers.get("referer", ""))
                origin = f"{referer.scheme}://{referer.netloc}"
            if origin is not None and origin not in configuration.origins:
                return JSONResponse(
                    status_code=403, content={"detail": "Browser origin is not allowed"}
                )
            if request.url.path=='/internal/publications/v2/stage' and request.method=='POST':
                body=bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body)>publication_bridge.MAX_BUNDLE_BYTES:
                        return JSONResponse(status_code=413,content={'detail':'Publication bundle exceeds 12 MiB'})
                request._body=bytes(body)
        # Optional host reporting stays outside the durable transaction owner.
        # A pending essential request remains protected even if its browser is suspended.
        essential = request.url.path.startswith("/api/") and request.url.path != "/api/study/activity" and request.method != "OPTIONS"
        with database.scope():
            if essential:
                async with request_activity():
                    return await call_next(request)
            return await call_next(request)

    # Host validation covers preflight too; CORS must wrap authority failures so
    # an allowed browser can observe 401/403 and reconnect without losing work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configuration.origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Tempo-Workspace",
        ],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(configuration.hosts))


    @app.exception_handler(ReviewConflict)
    async def conflict(request, error):
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.detail,
                "code": error.code,
                "retryable": error.retryable,
            },
        )

    async def database_unavailable(request, error):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Learning database unavailable; retain the original operation identity and retry"
            },
            headers={"Retry-After": "1"},
        )

    app.add_exception_handler(psycopg.OperationalError, database_unavailable)
    app.add_exception_handler(psycopg.InterfaceError, database_unavailable)
    app.add_exception_handler(PoolTimeout, database_unavailable)
    app.add_exception_handler(psycopg.errors.QueryCanceled, database_unavailable)
    app.add_exception_handler(psycopg.errors.LockNotAvailable, database_unavailable)
    app.add_exception_handler(psycopg.errors.TransactionTimeout, database_unavailable)

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.post("/api/study/activity")
    async def study_activity(session_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
                             expires_in_ms: int = Query(default=3000, ge=1, le=3000)):
        try:
            activity.record(session_id, expires_in_ms)
        except (OSError, RuntimeError):
            return JSONResponse(status_code=503, content={"available": False})
        return {"available": True, "expires_in_ms": expires_in_ms}

    @app.get("/health/ready")
    def ready():
        try:
            with connection(read_only=True) as db:
                applied, expected = storage.migration_state(db)
                if applied != expected:
                    raise RuntimeError("Learning migration history is not current")
        except (psycopg.Error, PoolTimeout, RuntimeError):
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready", "workspace_id": configuration.workspace_id}

    @app.get("/api/operations/{operation_id}")
    def operation(operation_id: str):
        return gateway.read_operation(operation_id,publication=False)

    @app.get("/api/study/bootstrap")
    def bootstrap():
        with connection(read_only=True) as db:
            return {"schema_version": 1, "workspace_id": configuration.workspace_id,
                    "timezone": configuration.timezone, "local_date": date.today().isoformat(),
                    "settings": study_bootstrap.read_settings(db)}

    @app.get("/api/study/settings")
    def study_settings():
        with connection(read_only=True) as db:
            return study_bootstrap.read_settings(db)

    @app.post("/api/study/settings")
    def save_study_settings(request: study_bootstrap.StudySettingsUpdate,
                            idempotency_key: str = Header(...)):
        values = request.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise HTTPException(422, "Supply at least one non-null Study setting")
        return gateway.dispatch("study.settings.update",
                                values, idempotency_key)

    @app.get("/api/tactics/progress")
    def tactics_progress():
        with connection(read_only=True) as db:
            return study_bootstrap.tactic_progress(db)

    @app.get("/api/cards/{identifier}/teaching")
    def teaching(identifier: str):
        with connection(read_only=True) as db:
            return teaching_commands.read_teaching(db, identifier)

    @app.post("/api/cards/{identifier}/teaching")
    def save_teaching(identifier: str, request: teaching_commands.TeachingStateRequest,
                      idempotency_key: str = Header(...)):
        return gateway.dispatch("cards.teaching.record", {
            "card_id": identifier, "teaching_state": request.model_dump()}, idempotency_key)

    @app.get("/api/cards/{identifier}/move-membership")
    def move_membership(identifier: str, revision: int, fen: str, move_uci: str):
        with connection(read_only=True) as db:
            return study_bootstrap.move_membership(db, identifier, revision, fen, move_uci)

    @app.post("/api/operations/{operation_id}/retry")
    def retry(operation_id: str):
        receipt = gateway.read_operation(operation_id,publication=False)
        if receipt["state"] == "unknown":
            raise HTTPException(404, "Unknown operation")
        if receipt["state"] == "failed":
            raise HTTPException(
                409, "Terminal failed operations require a new reconciliation operation"
            )
        gateway.execute(operation_id,publication=False)
        return gateway.operation_result(operation_id)

    def queue_payload(limit=20, include_opening_evidence=False):
        day = date.today().isoformat()
        gateway.dispatch(
            "queue.ensure_current",
            {"queue_date": day},
            idempotency_key="queue-load:" + day + ":" + uuid.uuid4().hex,
        )
        # A bounded foreground assist starts ordinary study even without a maintenance process.
        for _ in range(2):
            safe_run_once()
        with connection(read_only=True) as db:
            window = queue_application.read_queue_window(db, day, limit=limit)
        cards = window.cards
        projection = window.projection
        count = window.available_count
        if cards:
            head = cards[0]
            issued = gateway.dispatch(
                "queue.issue_head",
                {
                    "queue_date": day,
                    "entry_id": head["queue_entry_id"],
                    "card_id": head["id"],
                    "expected_revision": head["revision"],
                },
                idempotency_key="issue:" + uuid.uuid4().hex,
            )
            if not issued["issued"]:
                raise HTTPException(409, "Queue changed before issuance; reload")
        if include_opening_evidence:
            evidence.queue_manifests(cards)
        return {
            "local_date": day,
            "cards": cards,
            "count": count,
            "diagnostics": [],
            "projection": {
                key: projection[key]
                for key in (
                    "queue_date",
                    "state",
                    "generation",
                    "updated_at",
                    "refresh_pending",
                    "last_error",
                )
            }
            if projection
            else {
                "state": "refreshing",
                "generation": 0,
                "refresh_pending": 1,
                "last_error": None,
                "updated_at": None,
            },
        }

    @app.get("/api/queue/today")
    def today(include_opening_evidence: bool = False):
        return queue_payload(200, include_opening_evidence)

    @app.get("/api/queue/prepared")
    def prepared(include_opening_evidence: bool = False):
        return {
            **queue_payload(200, include_opening_evidence),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/queue/window")
    def window(
        limit: int = Query(20, ge=1, le=20), include_opening_evidence: bool = False
    ):
        return queue_payload(limit, include_opening_evidence)

    @app.get("/api/queue/entries/{entry_id}/state")
    def entry_state(entry_id: int):
        with connection(read_only=True) as db:
            row = storage.queue_entry_state(db, entry_id)
            head = storage.available_head(db, date.today().isoformat(), queue_application.MISS_REASON)
        return {
            "state": "unavailable"
            if not row or row["queue_date"] != date.today().isoformat()
            else "completed"
            if row["status"] == "complete"
            else "head"
            if head and head[0] == entry_id
            else "queued"
            if row["status"] == "queued"
            else "unavailable",
            "attempt_failed": bool(row["attempt_failed"]) if row else False,
        }

    @app.post("/api/queue/entries/{entry_id}/{action}")
    def queue_edit(
        entry_id: int,
        action: str,
        request: QueueAttemptFailureRequest | None = None,
        idempotency_key: str | None = Header(None),
    ):
        if action not in ("fail", "bury"):
            raise HTTPException(404, "Unknown queue action")
        return gateway.dispatch(
            "queue.attempt_failed" if action == "fail" else "queue.bury",
            {
                "entry_id": entry_id,
                **(request.model_dump(exclude_none=True) if request else {}),
            },
            idempotency_key,
        )

    def review_payload(identifier, request):
        payload = {"card_id": identifier, "review": request.durable_payload()}
        if request.opening_evidence_completion:
            payload["prepared_manifest"] = evidence.prepare_checkpoint(
                request.opening_evidence_completion
            )
        return payload

    @app.post("/api/cards/{identifier}/review")
    def review(
        identifier: str,
        request: ReviewRequest,
        idempotency_key: str | None = Header(None),
    ):
        return gateway.dispatch(
            "cards.review", review_payload(identifier, request), idempotency_key
        )

    @app.post("/api/cards/{identifier}/review/reconcile")
    def review_reconcile(
        identifier: str,
        request: ReviewRequest,
        idempotency_key: str | None = Header(None),
    ):
        if not request.attempt_id or request.queue_entry_id is None:
            raise HTTPException(
                422, "Reconciliation requires the original attempt and queue entry IDs"
            )
        return gateway.dispatch(
            "cards.review.reconcile",
            review_payload(identifier, request),
            idempotency_key,
        )

    @app.post("/api/opening-evidence/checkpoints")
    def checkpoint(
        request: OpeningEvidenceCheckpoint, idempotency_key: str | None = Header(None)
    ):
        return gateway.dispatch(
            "opening_evidence.checkpoint",
            {
                "checkpoint": request.model_dump(mode="json"),
                "prepared_manifest": evidence.prepare_checkpoint(request),
            },
            idempotency_key,
        )

    @app.get("/api/opening-evidence/attempts/{attempt_id}")
    def checkpoint_status(attempt_id: str):
        with connection(read_only=True) as db:
            attempt = storage.opening_attempt(db, attempt_id)
        if attempt is None:
            raise HTTPException(404, "Opening attempt evidence not found")
        with connection(read_only=True) as db:
            events = [
                json.loads(row[0])
                for row in storage.opening_events(db, attempt_id)
            ]
        return {**dict(attempt), "events": events}

    @app.get("/api/opening-evidence/decisions/{decision_id}")
    def decision(
        decision_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        with connection(read_only=True) as db:
            return evidence.decision_evidence(
                db, decision_id, offset=offset, limit=limit
            )

    @app.get("/api/studies/{study_id}/exercises/{exercise_id}/present")
    def present(study_id: str, exercise_id: str):
        with connection(read_only=True) as db:
            row = storage.published_study_exercise(db, exercise_id, study_id)
        if row is None:
            raise HTTPException(404, "Exercise unavailable")
        specification = json.loads(row["specification_json"])
        safe = {
            key: specification[key]
            for key in (
                "mode",
                "grading_policy",
                "criterion",
                "candidate_region",
                "start_square",
                "target_squares",
                "minimum_hops",
                "maximum_hops",
                "hop_rule",
                "occupancy_rule",
                "options",
            )
            if key in specification
        }
        return {
            "id": exercise_id,
            "revision": row["current_revision"],
            "type": specification["type"],
            "prompt": specification["prompt"],
            "hint": specification.get("hint", ""),
            "fen": row["fen"],
            **safe,
        }

    @app.post("/api/studies/{study_id}/exercises/{exercise_id}/attempts")
    def typed_attempt(
        study_id: str,
        exercise_id: str,
        request: StudyAttemptRequest,
        idempotency_key: str | None = Header(None),
    ):
        return gateway.dispatch(
            "studies.attempts.submit",
            {
                "study_id": study_id,
                "exercise_id": exercise_id,
                "attempt": request.model_dump(mode="json"),
            },
            idempotency_key or request.attempt_id,
        )

    @app.post(
        "/api/studies/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/self-assess"
    )
    def assessment(
        study_id: str,
        exercise_id: str,
        attempt_id: str,
        request: StudySelfAssessmentRequest,
        idempotency_key: str | None = Header(None),
    ):
        return gateway.dispatch(
            "studies.attempts.self_assess",
            {
                "study_id": study_id,
                "exercise_id": exercise_id,
                "attempt_id": attempt_id,
                "assessment": request.model_dump(mode="json"),
            },
            idempotency_key or attempt_id + ":self-assess",
        )

    @app.get(
        "/api/studies/{study_id}/exercises/{exercise_id}/attempts/{attempt_id}/feedback"
    )
    def feedback(study_id: str, exercise_id: str, attempt_id: str):
        with connection(read_only=True) as db:
            row = storage.committed_study_specification(db, attempt_id, exercise_id, study_id)
        if row is None:
            raise HTTPException(404, "Committed attempt not found")
        return {"specification": json.loads(row[0])}

    @app.post("/api/tactics/attempt")
    def tactic(
        request: TacticAttemptRequest, idempotency_key: str | None = Header(None)
    ):
        if not request.attempt_id and not idempotency_key:
            raise HTTPException(422, "Send attempt_id or Idempotency-Key")
        if not request.attempt_id:
            request = request.model_copy(
                update={
                    "attempt_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, "tempo:tactic:" + idempotency_key
                        )
                    )
                }
            )
        training_fen, solution = validate_puzzle_record(
            {"FEN": request.source_fen, "Moves": request.moves}
        )
        return gateway.dispatch(
            "tactics.attempt.submit",
            {
                "request": request.model_dump(mode="json"),
                "pack_id": request.deck_id,
                "training_fen": training_fen,
                "solution": solution,
            },
            idempotency_key or "tactic-attempt:" + request.attempt_id,
        )

    @app.get("/api/defense-exercises/{candidate_id}")
    def defense_read(candidate_id: str):
        return read_defense_exercise(candidate_id)

    @app.post("/api/defense-exercises/{candidate_id}/recognition")
    def recognition(
        candidate_id: str,
        request: DefenseRecognitionRequest,
        idempotency_key: str | None = Header(None),
    ):
        return gateway.dispatch(
            "defense.recognition.submit",
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            idempotency_key or "defense-recognition:" + request.attempt_id,
        )

    @app.post("/api/defense-exercises/{candidate_id}/attempt")
    def defense_attempt(
        candidate_id: str,
        request: DefenseAttemptRequest,
        idempotency_key: str | None = Header(None),
    ):
        return gateway.dispatch(
            "defense.attempt.submit",
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            idempotency_key or "defense-attempt:" + request.attempt_id,
        )

    @app.post("/api/endgames/probe")
    async def probe(request: EndgameProbeRequest):
        raise HTTPException(410, "On-demand endgame calculation has been removed")

    @app.get("/api/endgames/templates")
    def templates():
        return {"templates": endgame_store.active_templates()}

    @app.post("/api/endgames/templates/{identifier}/attempt")
    def endgame_attempt(identifier: str, idempotency_key: str | None = Header(None)):
        # Recover only an already-recorded immutable position. Never generate a
        # replacement or create a new operation identity after removal.
        if idempotency_key:
            prior = endgame_store.prior_attempt(idempotency_key)
            if prior:
                payload = json.loads(prior["payload_json"])
                if (prior["command_name"] != "endgames.attempt.create"
                        or payload.get("template_id") != identifier):
                    raise HTTPException(409, "Operation ID was already used for another request")
                return gateway.dispatch("endgames.attempt.create", payload, idempotency_key)
        raise HTTPException(410, "On-demand endgame calculation has been removed; saved attempts remain available")

    @app.get("/internal/publications/operations/{operation_id}")
    def publication_operation(operation_id: str):
        return gateway.read_operation(operation_id,publication=True)

    @app.post('/internal/publications/operations/{operation_id}/abort')
    def publication_abort(operation_id: str, payload: dict):
        return publication_bridge.abort_operation(operation_id, payload, configuration.workspace_id)

    @app.post("/internal/publications/operations/{operation_id}/retry")
    def publication_retry(operation_id: str):
        receipt = gateway.read_operation(operation_id,publication=True)
        if receipt["state"] == "unknown":
            raise HTTPException(404, "Unknown operation")
        if receipt["state"] == "failed":
            raise HTTPException(409, "Terminal publication failure")
        with connection(read_only=True) as db:
            intent=storage.operation_intent(db, operation_id)
        if intent and intent[0]=='publication.activate.v2':
            return publication_bridge.dispatch_activation(json.loads(intent[1]),operation_id)
        gateway.execute(operation_id,publication=True)
        return gateway.operation_result(operation_id)

    @app.get("/internal/publications/{publication_id}/status")
    def publication_status(publication_id: str, generation: int | None = Query(None,ge=1)):
        with connection(read_only=True) as db:
            return publication_bridge.status(db, publication_id, configuration.workspace_id,generation)

    @app.post("/internal/publications/v2/stage")
    def publication_stage_v2(payload: publication_bridge.PublicationBundleV2):
        with connection() as db:
            return publication_bridge.stage(db, payload, configuration.workspace_id)

    @app.post("/internal/publications/v2/activate")
    def publication_activate_v2(payload: dict, idempotency_key: str | None = Header(None)):
        operation_id = idempotency_key if idempotency_key is not None else payload.get('activation_id')
        publication_bridge.validate_operation_request('publication.activate.v2', payload, operation_id)
        return publication_bridge.dispatch_activation(payload, operation_id)

    @app.post("/internal/publications/stage")
    def publication_stage(payload: PublicationBundle):
        with connection() as db:
            return stage(
                db, payload.model_dump(mode="json"), configuration.workspace_id
            )

    @app.post("/internal/publications/activate")
    def publication_activate(payload: dict, idempotency_key: str | None = Header(None)):
        return gateway.dispatch(
            "publication.activate", payload, idempotency_key or payload["activation_id"]
        )

    @app.post("/internal/publications/retire")
    def publication_retire(payload: dict, idempotency_key: str | None = Header(None)):
        operation_id = idempotency_key if idempotency_key is not None else payload.get('retirement_id')
        publication_bridge.validate_operation_request('publication.retire', payload, operation_id)
        return gateway.dispatch(
            "publication.retire", payload, operation_id
        )

    @app.post("/internal/evidence")
    def evidence_receive(payload: EvidenceDelivery):
        with connection(background=True, intake=True) as db:
            return receive(
                db, payload.model_dump(mode="json"), configuration.workspace_id, background=True
            )

    return app
