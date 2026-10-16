"""Small scoped HTTP surface; provider work runs outside request transactions."""

import asyncio
from contextlib import asynccontextmanager
import hmac

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from tempo_analysis_jobs_contracts.v1 import MaiaResult, JobStatus

from .config import Configuration
from .errors import DomainError
from .models import RequestInput


class BodyLimitMiddleware:
    """Bound actual streamed input before JSON decoding, even without a length."""

    def __init__(self, app, maximum_bytes=1024 * 1024):
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        content = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(content) + len(chunk) > self.maximum_bytes:
                return await JSONResponse({"error": "input_size_exceeded"}, status_code=413)(scope, receive, send)
            content.extend(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def bounded_receive():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(content), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class RevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command_id: str = Field(min_length=1, max_length=200)
    expected_revision: int | None = Field(default=None, ge=1, le=2**53 - 1)


class CredentialCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_generation: int = Field(ge=0, le=2**53 - 1)


def create_app(configuration: Configuration, store=None, worker=None):
    @asynccontextmanager
    async def lifespan(app):
        owned_store = store is None
        task = None
        client = None
        try:
            try:
                if owned_store:
                    from .store import Store

                    app.state.store = Store(configuration)
                else:
                    app.state.store = store
                app.state.store.validate_startup()
            except Exception:
                raise RuntimeError("intelligence_startup_failed") from None
            from .credentials import PrivateCredentials

            app.state.credentials = PrivateCredentials(configuration.credential_file)
            app.state.worker = worker
            if configuration.worker_enabled:
                if worker is None:
                    from .provider import ExplorerClient
                    from .worker import Worker

                    client = ExplorerClient(configuration.provider_base_url)
                    app.state.worker = Worker(app.state.store, app.state.credentials, client)

                async def run_background():
                    while True:
                        try:
                            progressed = await app.state.worker.work_once()
                            app.state.worker_error = None
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            # Provider details and secrets are never logged here.
                            app.state.worker_error = "background_work_unavailable"
                            progressed = False
                        await asyncio.sleep(0 if progressed else configuration.poll_interval)

                task = asyncio.create_task(run_background())
            yield
        finally:
            shutdown_failed = False
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    app.state.worker_error = "background_work_unavailable"
            if client is not None and hasattr(client, "aclose"):
                try:
                    await client.aclose()
                except Exception:
                    shutdown_failed = True
            if owned_store and hasattr(app.state, "store"):
                try:
                    app.state.store.close()
                except Exception:
                    shutdown_failed = True
            if shutdown_failed:
                raise RuntimeError("intelligence_shutdown_failed") from None

    app = FastAPI(title="Tempo Intelligence", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(configuration.hosts))
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def authorize(request: Request, next_handler):
        async def safely_forward():
            try:
                return await next_handler(request)
            except Exception:
                # ServerErrorMiddleware rethrows generic exception handlers to
                # the HTTP server. Catch here so private driver details cannot
                # reach its exception logger after a sanitized response.
                return JSONResponse({"error": "intelligence_unavailable"}, status_code=503)

        if request.url.path == "/healthz":
            return await safely_forward()
        if request.url.path.startswith("/v1/credentials/"):
            expected = configuration.operator_token
        elif request.url.path in {"/v1/maia/results", "/v1/maia/status"}:
            expected = configuration.worker_token
        else:
            expected = configuration.caller_token
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + expected).encode("utf-8")):
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if request.headers.get("x-tempo-workspace", configuration.workspace_id) != configuration.workspace_id:
            return JSONResponse({"error": "workspace_mismatch"}, status_code=403)
        return await safely_forward()

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Pydantic's usual error payload echoes rejected input, including extras.
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(Exception)
    async def unavailable(request, error):
        return JSONResponse({"error": "intelligence_unavailable"}, status_code=503)

    @app.get("/healthz")
    def health():
        app.state.store.health()
        return {"status": "ready", "workspace_id": configuration.workspace_id}

    @app.post("/v1/requests")
    def submit(payload: RequestInput):
        return app.state.store.submit(payload)

    @app.get("/v1/requests/{request_id}")
    def inspect(request_id: str):
        return app.state.store.inspect(request_id)

    @app.get("/v1/evidence/{evidence_id}")
    def evidence(evidence_id: str):
        return app.state.store.evidence(evidence_id)

    @app.post("/v1/requests/{request_id}/refresh")
    def refresh(request_id: str, payload: RevisionCommand):
        return app.state.store.refresh(request_id, payload.command_id, payload.expected_revision)

    @app.post("/v1/requests/{request_id}/retry")
    def retry(request_id: str, payload: RevisionCommand):
        return app.state.store.retry(request_id, payload.command_id, payload.expected_revision)

    @app.get("/v1/requests/{request_id}/maia-jobs")
    def maia_jobs(request_id: str):
        return app.state.store.maia_jobs(request_id)

    @app.post("/v1/maia/results")
    def accept_maia(payload: MaiaResult):
        return app.state.store.accept_maia(payload)

    @app.post("/v1/maia/status")
    def maia_status(payload: JobStatus):
        return app.state.store.maia_status(payload)

    @app.post("/v1/credentials/lichess-explorer/reload")
    def credential_reload(payload: CredentialCommand):
        from .credentials import CredentialError

        try:
            material = app.state.credentials.load()
        except CredentialError as error:
            raise DomainError(error.code, status=409) from None
        return app.state.store.credential_reload(material, payload.expected_generation)

    @app.post("/v1/credentials/lichess-explorer/revoke")
    def credential_revoke(payload: CredentialCommand):
        return app.state.store.credential_revoke(payload.expected_generation)

    return app
