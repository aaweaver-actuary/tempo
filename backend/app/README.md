# Retained backend

FastAPI and Celery serve one PostgreSQL application: Study, Tactics, repertoire editing/import, and minimal repertoire-based Game Review.

- `main.py` owns HTTP assembly and read/command admission.
- `tasks.py` explicitly registers retained commands and dispatches queue, graph and integrity slices. Original checkpoint commands remain recoverable.
- `study/position_learning/` owns position schedules, first-response policy, immutable issuance and review effects. `study/position_checkpoint.py` owns the existing attempt/event/checkpoint boundary.
- `services/queue_*`, `queue_commands.py` and `services/learning_review_adapter.py` compose ordinary queue/review behavior and preserve original attempts and receipts.
- `repertoire/` owns authored-position projection and source validation; current editing/import commands retain integrity and graph publication fences.
- `games/review.py` replays PGN or existing saved moves and selects a first repertoire departure. It has no background producer or Study effect.
- `database/tempo_storage` owns concrete native PostgreSQL operations and admitted transaction scopes. No dialect translator or generic repository layer remains.

See the current [source maps](../../docs/architecture/diagrams/README.md) and [reduction audit](../../docs/architecture/reduction-pass2/README.md). Source compilation and focused checks do not establish native or installed acceptance.
