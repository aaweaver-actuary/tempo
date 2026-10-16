"""One bounded Explorer slice. Network work never holds a pool connection."""
import asyncio
from datetime import datetime, timezone

from .credentials import CredentialError
from .provider import ProviderFailure


class Worker:
    def __init__(self, store, credential_adapter, client):
        self.store = store
        self.credentials = credential_adapter
        self.client = client

    async def work_once(self):
        claim = self.store.claim()
        if claim is None:
            return False
        try:
            material = self.credentials.load()
            registration = claim["credential"]
            if material.registration_id != registration["registration_id"]:
                self.store.fail(claim,"credential_registration_mismatch")
                return True
            if material.expires_at and material.expires_at <= datetime.now(timezone.utc):
                self.store.fail(claim,"credential_expired")
                return True
            payload = await self.client.fetch(claim["descriptor"],material.token)
            self.store.publish(claim,payload)
        except CredentialError as error:
            self.store.fail(claim,error.code)
        except ProviderFailure as error:
            self.store.fail(claim,error.code,retryable=error.retryable,retry_at=error.retry_at,auth_failed=error.auth_failed)
        except asyncio.CancelledError:
            self.store.fail(claim,"request_cancelled",cancelled=True)
            raise
        except Exception:
            self.store.fail(claim,"provider_unavailable",retryable=True)
        return True
