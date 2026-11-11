"""Existing bounded worker-slot Redis records and short-deadline client factory."""
from functools import lru_cache
import os

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry


@lru_cache(maxsize=1)
def _diagnostic_client():
    # Telemetry must never inherit the admission client's one-second network waits.
    return redis.Redis.from_url(os.environ["TEMPO_REDIS_URL"], socket_connect_timeout=0.005,
                               socket_timeout=0.005, retry=Retry(NoBackoff(), 0))


def publish_worker_stage(server, slot: int, encoded_stage: str):
    return server.set(f"tempo:diagnostics:worker:{slot}", encoded_stage, ex=15)


def worker_stage_values(server):
    return server.mget([f"tempo:diagnostics:worker:{slot}" for slot in range(16)])
