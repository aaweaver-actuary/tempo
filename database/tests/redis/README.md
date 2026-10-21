# Disposable Redis proofs

These are existing native Redis/Celery/Kombu and persisted-readiness proofs, physically moved from `scripts/`. Their native fixture operations, Lua invocations, assertions and failure/cleanup ordering are unchanged. Current proof callers use these paths directly; old dedicated `scripts/` entry points are removed. This relocation does not run any proof or establish current infrastructure qualification.

- [`check_redis_background_wakes.py`](check_redis_background_wakes.py): coalescing, delivery fencing and AOF restart fixtures; retains `TEMPO_TEST_INSTANCE=disposable` and invocation-owned queue/key cleanup.
- [`check_redis_socket_deadlines.py`](check_redis_socket_deadlines.py): bounded broker publication outage and connection recovery, retaining its disposable guard, socket proxy and UUID-qualified cleanup.
- [`check-redis-readiness.mjs`](check-redis-readiness.mjs): persisted Redis LOADING/healthcheck fixture, retaining the disposable target guard, configuration release/restoration and existing worker-stop/lifecycle callbacks. These lifecycle interactions are test composition, not a new runtime domain dependency.

Storage is operational Redis wake ownership/publication metadata, Kombu queue/unacked state and fixture-only keys; this move changes no schema, key format or application behavior. The owning runtime adapters remain in [`tempo_legacy_database/adapters/redis_control`](../../tempo_legacy_database/adapters/redis_control/README.md). No live schema or data has been inspected.
