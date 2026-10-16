"""One bounded Explorer slice. Network work never holds a pool connection."""
import asyncio
from datetime import datetime, timezone

from .credentials import CredentialError
from .provider import ProviderFailure
from .runtime import DatabaseRunner, drain_operation


class Worker:
    def __init__(self, store, credential_adapter, client, runner=None):
        self.store = store
        self.credentials = credential_adapter
        self.client = client
        self.runner = runner
        self.lock = asyncio.Lock()
        self.closed = False

    async def work_once(self):
        async with self.lock:
            if self.closed:
                raise RuntimeError("worker_closed")
            runner = self.runner or DatabaseRunner()
            try:
                try:
                    return await self._work_once(runner)
                except asyncio.CancelledError:
                    if self.runner is None:
                        closing = asyncio.create_task(runner.aclose())
                        try:
                            await drain_operation(closing)
                        except Exception:
                            pass
                    raise
            finally:
                # Standalone slices must not leak an executor into pool shutdown.
                if self.runner is None and not runner.closed:
                    await runner.aclose()

    async def _work_once(self, runner):
        claim = None
        published = False

        def claimed(value):
            nonlocal claim
            claim = value

        def publication_finished(value):
            nonlocal published
            published = bool(value)

        try:
            await runner.run(self.store.claim, on_result=claimed)
            if claim is None:
                return False
            failure = None
            try:
                material = self.credentials.load()
                registration = claim["credential"]
                if material.registration_id != registration["registration_id"]:
                    failure = ("credential_registration_mismatch", {})
                elif material.expires_at and material.expires_at <= datetime.now(timezone.utc):
                    failure = ("credential_expired", {})
                else:
                    payload = await self.client.fetch(claim["descriptor"], material.token)
            except CredentialError as error:
                failure = (error.code, {})
            except ProviderFailure as error:
                failure = (error.code, dict(retryable=error.retryable, retry_at=error.retry_at,
                                            auth_failed=error.auth_failed))
            except Exception:
                failure = ("provider_unavailable", {"retryable": True})
            # Database errors belong to the background loop, not the provider.
            if failure is not None:
                code, options = failure
                await runner.run(self.store.fail, claim, code, **options)
            else:
                await runner.run(self.store.publish, claim, payload, on_result=publication_finished)
        except asyncio.CancelledError:
            if claim is not None and not published:
                # Own cleanup through its checkpoint even on repeated cancel.
                cleanup = asyncio.create_task(runner.run(
                    self.store.fail, claim, "request_cancelled", cancelled=True))
                try:
                    await drain_operation(cleanup)
                except Exception:
                    pass  # Expiring fenced leases are the recovery fallback.
            raise
        return True

    async def aclose(self):
        self.closed = True
        async with self.lock:
            pass  # The active slice drained before its owning Store may close.
