# Legacy bridge database qualification tooling

**Current source and plans only; native execution unverified for this relocation.** This is the existing finite artifact/module-native/backend-native harness moved intact from repository scripts. It owns resource-proof preparation, exact test inventory, staged source/artifact verification and cleanup receipts. It is not an application runtime, a new qualification framework or permission to allocate resources.

| Existing tool | Responsibility |
| --- | --- |
| [qualify_legacy_bridge.py](qualify_legacy_bridge.py) | Existing six-package artifact and five-case remediation native windows, immutable tool/artifact identities, fixed resource/deadline checks and verified cleanup. The artifact mode also covers non-database contract packages; it stays with the same finite bridge qualification owner to preserve one resource/deadline authority. |
| [qualify_legacy_backend.py](qualify_legacy_backend.py) | Existing six-case legacy backend HTTP/Celery/Redis/PostgreSQL proof, installed artifact provenance, controlled fixture manifest and cleanup. Native test node paths now refer to [root database tests](../../tests/legacy_bridge/README.md). |
| [legacy_qualification_staging.py](legacy_qualification_staging.py) | Existing tracked-source allowlist, bounded owned staging, byte inventories, qualified environment verification and nonsecret receipt copyback. Required runner files and native test subtree use their new root locations. |
| [legacy-bridge-window.mjs](legacy-bridge-window.mjs) | Existing managed development-budget wrapper/watchdog. It still uses the shared repository [budget owner](../../../../scripts/development-budget.mjs) and [Docker environment adapter](../../../../scripts/development-docker.mjs); their implementations were not copied or moved. |

Pure plan inspection from repository root is available without allocating infrastructure:

```sh
python database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_bridge.py --describe
python database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_backend.py --describe
```

All actual resource modes still require the existing explicit finite window, managed owner and immutable qualified inputs. No native mode, container, fixture connection, service, socket, installation or credential action ran during this file move. Existing grant/endpoint/manifest/cleanup predicates and caps remain unchanged. The native manifest accepts precisely the existing controlled read-only `/source/database` import path, alongside `/source/backend`, `/source/scripts` and the original qualified-artifact path rule; no broader source directory or network target was added.

The policy/inventory checker remains [Learning's existing qualification.py](../../../../services/learning/application/qualification.py). The five remediation native node references follow their owner's move to [database-owned native tests](../../../tempo_remediation_database/tests/native/test_postgres.py). Independent packages keep their runtime ownership; physical tooling colocation grants no cross-owner permissions.

For actual stored fields, keys and enforced versus logical relationships, use the [legacy schema dictionary index](../../README.md) and [remediation schema](../../../tempo_remediation_database/README.md). No schema or stored payload changed.

The three existing pure plan/target-guard test files under `backend/tests` pass 131 cases, including the new exact-path acceptance/rejection regression. Guarded source-only collection finds the unchanged six legacy backend, fifteen Discovery acceptance and four queue-admission cases. Collection is not native or installed-artifact qualification. The review interpreter lacked the installed `tempo_a_bridge`; its existing source directory was supplied only to the task-local collection process. Production native harness artifact-origin checks were preserved.
