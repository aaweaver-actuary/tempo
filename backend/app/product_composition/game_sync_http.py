"""Bind Games sync transport to the existing runtime and storage capabilities."""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from typing import ContextManager, Generic, TypeVar

from fastapi import Response
from app.games.sync.interfaces import GameSyncEnqueueResponse, GameSyncRequest, GameSyncStatusResponse

Database = TypeVar('Database')
Row = Mapping[str, object]


@dataclass(frozen=True)
class GameSyncHttpCapabilities(Generic[Database]):
    postgres_configured: Callable[[], bool]
    dispatch: Callable[[str, dict[str, object], str | None], object]
    in_background: Callable[[], bool]
    enqueue: Callable[..., str]
    wake: Callable[[], None]
    connection: Callable[..., ContextManager[Database]]
    job_status: Callable[[Database, str], Row]
    provider_states: Callable[[Database], Sequence[Row]]
    latest_job: Callable[[Database], Row | None]
    serialize_job: Callable[[dict[str, object] | None], object]


def create_game_sync_http_binding(capabilities: GameSyncHttpCapabilities[Database]):
    def enqueue(request: GameSyncRequest, idempotency_key: str | None) -> GameSyncEnqueueResponse | Response:
        if capabilities.postgres_configured():
            result = capabilities.dispatch('games.sync.enqueue', request.model_dump(mode='json'), idempotency_key)
            return result if isinstance(result, Response) else GameSyncEnqueueResponse.model_validate(result)
        background = capabilities.in_background()
        job_id = capabilities.enqueue(request, background=background)
        capabilities.wake()
        with capabilities.connection(background=background) as database:
            job = capabilities.job_status(database, job_id)
        return GameSyncEnqueueResponse.model_validate(
            {'imported': 0, 'job_id': job_id, 'status': job['status'], 'providers': {}})

    def read_status() -> GameSyncStatusResponse:
        with capabilities.connection(background=capabilities.in_background()) as database:
            rows = capabilities.provider_states(database)
            job_row = capabilities.latest_job(database)
        providers = []
        for row in rows:
            provider = dict(row)
            raw_result = provider.pop('last_result_json', None)
            provider['last_result'] = json.loads(raw_result) if raw_result else None
            providers.append(provider)
        return GameSyncStatusResponse.model_validate({
            'providers': providers,
            'active_filters': {'days': 90, 'speeds': ['blitz', 'rapid', 'classical'], 'rated_only': True},
            'active_job': capabilities.serialize_job(dict(job_row) if job_row else None),
        })

    return enqueue, read_status
