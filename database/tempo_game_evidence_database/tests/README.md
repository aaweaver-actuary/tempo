# Game Evidence database tests

**Current source; native contracts retained and not executed by this relocation.** [native/test_postgres.py](native/test_postgres.py) is the existing service integration suite, moved intact apart from its source-checkout fixture path. Native test functions, SQL, fixtures, markers, locks, concurrency/restart schedules and assertions retain their original implementation. PGN/snapshot fixtures remain service-owned [application fixtures](../../../services/game-evidence/fixtures), with no duplicate corpus.

Use only the [existing owned native runner](../tooling/README.md) for the native suite. It preserves disposable owner/runtime resource setup, environment isolation and verified teardown, and selects the same native file at its new location. This move does not authorize invoking it or establish fresh native passes, credentials/permission acceptance, deployment or optional-feature resource isolation.

[test_source_fingerprints.py](test_source_fingerprints.py) owns the existing pure native-source fingerprint regression moved from service `test_boundary.py`, plus a narrow proof that artifact fingerprints cover moved root inputs. Their bodies compile only hash expressions/loops against synthetic files; they do not import or execute the runner, native test module or build orchestration. Normal pytest package collection uses the installed component runtime, with no connection, network or service startup. The regular installed-import guard remains in the [service test suite](../../../services/game-evidence/tests/test_boundary.py). Select these two pure checks separately in a source checkout:

```sh
services/game-evidence/.venv/bin/python -m pytest database/tempo_game_evidence_database/tests/test_source_fingerprints.py -q
```

The native runner also selects this pure metadata file, preserving an owning regular discovery path without a missing-file fallback or skip. Moving the one source-inspection test out of installed consumers reduces that service-only test count by one; its proof now belongs to this source/sdist database test category. Neither test is claimed as actual native verification.

The service build hook includes this directory and native tooling in its sdist and excludes both from runtime wheels. Native tooling remains source-checkout-only; no independently installed native test harness is introduced. Runtime resources and migration discovery keep their existing names. [Canonical schema diagrams and complete field/type/PK/composite/FK dictionaries](../schema/README.md) remain unchanged; test evidence never creates an inferred constraint or a live-schema claim.
