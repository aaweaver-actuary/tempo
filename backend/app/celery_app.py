"""Task routing for foreground commands and bounded background slices.

The queues have dedicated workers. Redis task priority alone is insufficient:
worker reservations and long tasks can still delay a foreground command.
"""

from __future__ import annotations

import os

from celery import Celery


broker_url = os.environ.get("TEMPO_REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("tempo", broker=broker_url, backend=broker_url, include=["app.tasks"])
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="foreground",
    task_routes={
        "app.tasks.execute_foreground_command": {"queue": "foreground"},
        "app.tasks.execute_background_slice": {"queue": "background"},
        "app.tasks.poll_background_tasks": {"queue": "background"},
    },
    beat_schedule={
        "poll-durable-background-tasks": {
            "task": "app.tasks.poll_background_tasks",
            "schedule": 1.0,
        },
    },
    broker_connection_retry_on_startup=True,
    result_expires=86_400,
    task_track_started=True,
)
