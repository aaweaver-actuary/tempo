# Reverse composition-root imports

| Source | Scope | Target | Location |
|---|---|---|---|
| app.background_worker | <module> | app.main | [line 12](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/background_worker.py#L12) |
| app.canonical_prefix_api | canonical_prefix_preview | app.main | [line 104](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/canonical_prefix_api.py#L104) |
| app.canonical_prefix_api | canonical_prefix_save | app.main | [line 128](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/canonical_prefix_api.py#L128) |
| app.command_dispatch | <module> | app.celery_app | [line 15](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/command_dispatch.py#L15) |
| app.main | <module> | app.celery_app | [line 32](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/main.py#L32) |
| app.postgres_readiness | <module> | app.celery_app | [line 10](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/postgres_readiness.py#L10) |
| app.review_commands | submit_review | app.main | [line 54](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/review_commands.py#L54) |
| app.services.opening_ranking_snapshot | _project_snapshot | app.main | [line 169](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/opening_ranking_snapshot.py#L169) |
| app.services.postgres_queue_refresh | _prepare_prioritized_openings | app.main | [line 360](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/postgres_queue_refresh.py#L360) |
| app.services.postgres_queue_refresh | _prepare_queue_randomization | app.main | [line 605](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/postgres_queue_refresh.py#L605) |
| app.services.postgres_queue_refresh | _publish_queue_randomization | app.main | [line 652](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/postgres_queue_refresh.py#L652) |
| app.services.postgres_queue_refresh | execute_postgres_queue_refresh_slice | app.main | [line 804](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/postgres_queue_refresh.py#L804) |
| app.services.queue_refresh_wakeup | wake_queue_refresh | app.celery_app | [line 41](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/queue_refresh_wakeup.py#L41) |
| app.services.study_attempts | finish_study_attempt | app.main | [line 44](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/services/study_attempts.py#L44) |
| app.study_routes | _queue_refresh | app.main | [line 89](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/study_routes.py#L89) |
| app.tasks | <module> | app.celery_app | [line 17](https://github.com/aaweaver-actuary/tempo/blob/02b65f229aaccc91ae7c03fd11efc8f3aedd2883/backend/app/tasks.py#L17) |
