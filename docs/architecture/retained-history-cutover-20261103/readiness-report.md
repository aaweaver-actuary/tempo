# Retained-history component handoff — 2026-11-03

**Initial representative mechanism: PASS. Programme cutover readiness: NO-GO.**
This records repository development and owned disposable component testing.
It is not a completed migration, assembled saved-review journey, release,
installation or activation. The canonical programme ledger is [#908](https://github.com/aaweaver-actuary/tempo/issues/908).
The [versioned contract](contract-v1.md), [state disposition ledger](state-ledger.csv)
and [consistency/recovery protocol](migration-state-machine.md) remain the public
handoffs. Minimum representative identity semantics were reviewed; the full
required-family ledger is explicitly provisional/BLOCK where owner facts are
missing, rather than an approved complete live mapping.

## Source and ownership

Remote main was checked at intake and after implementation and remained
`5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a`. Its checkout remained clean.
All code used isolated feature branches. The coordinator's combined executable
source tested below is `36ef3515cf45612bbea758298ef46d778d9c4389`.
Only report prose follows that proof; source/dependency inputs are unchanged.

T00 (Terra) validated all 22 prompts, existing paths, dependencies and an acyclic
DAG. P0A/C/D ran independently alongside Sol-owned W01. Existing #832/#881,
#886, #766 and #718 owners were preserved; the completed architecture audit was
consumed rather than repeated. R01/R02 retirement was not assigned or attempted.

The coordinator owns shared contracts, migration sequence/manifest, integration
and this handoff. `0004_retained_position.sql` is
`learning.retained_position/0001` after `learning.bridge/0001`;
`0005_queue_refresh_epochs.sql` is `learning.queue_epochs/0001` after the former.
No legacy migration, installed registration or production Compose was changed.
The canonical [staging persistence view](../../../database/tempo_learning_database/schema/retained-position/README.md)
and [queue lifecycle view](../../../database/tempo_learning_database/adapters/queue/README.md)
describe the changed source boundaries.

| Work | Source PR / pin | Result and remaining limit |
| --- | --- | --- |
| W01 | [#909](https://github.com/aaweaver-actuary/tempo/pull/909), `62e1b912` | Independent `tempo-learning-transfer-contracts` v1; minimum representative identity review PASS. Complete state ledger remains BLOCK on real owner facts/mappings. |
| P0C | [#910](https://github.com/aaweaver-actuary/tempo/pull/910), `9e161c27` | Source limits and reproducible synthetic capacity; no real graph equivalence. |
| W04A | [#911](https://github.com/aaweaver-actuary/tempo/pull/911), `e7e7a27e` | Native original Tactics progress/receipt replay subset; capture/pack/catalog transfer absent. |
| P0D | [#912](https://github.com/aaweaver-actuary/tempo/pull/912), `2cb48523` | Source writer/restart/credential/SQLite inventory and negative contracts; no enforceable fence. |
| W05A | [#913](https://github.com/aaweaver-actuary/tempo/pull/913), `2539a728` | Bounded passive original IndexedDB key/value inspection; no durable reconciliation/quarantine or authority translation. |
| W02A | [#914](https://github.com/aaweaver-actuary/tempo/pull/914), `39c7edae` | Representative source-state/fact copy, atomic batch replay, registered migration; staging remains non-serving. |
| W01B | [#915](https://github.com/aaweaver-actuary/tempo/pull/915), `f3cd3bfe` | Sol-reviewed unselected claimed-binding guard; native evidence readers/fence absent. Normal launcher unchanged. |
| W03A | [#916](https://github.com/aaweaver-actuary/tempo/pull/916), `43c0b335` | Queue epochs, task coalescing, frozen-token/preservation checks; not all source invalidations or retained issuance/review parity. |
| W02B | [#917](https://github.com/aaweaver-actuary/tempo/pull/917), `294efbe5` | Pure grade proposals and matching future review from retained FSRS; accepted-grade transaction/receipt port absent. |
| W09A | [#918](https://github.com/aaweaver-actuary/tempo/pull/918), `3d5d6381` | Native bounded complete synthetic generation and predecessor fence; actual retained mapping, source-edit race and overflow contract unproved. |
| W02C | [#919](https://github.com/aaweaver-actuary/tempo/pull/919), `db32c5ef` | Pure recombination/Italian holds; no executable target seed/activation owner. |

The dependency stack is #909 → #914 → #916; #917/#919 depend on the position
mechanism. Independent tests/guards depend on #909. Source integration preserved
every branch history and resolved only append-only regression inventory conflicts
by retaining all entries. No GitHub PR was merged.

## Actual proof

The owning native runner executed **9 cases, all passed**, on combined source
`36ef3515cf45612bbea758298ef46d778d9c4389`, host Python 3.12.12. It provisioned
only Learning's essential fixture, identity `tempo-learning-818e67b05100bccc`,
with PostgreSQL image
`sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81`
and observed 512 MiB memory / unrestricted CPU. The receipt records exact
identities, source/dependency inputs and commands:
`/private/tmp/tempo-cutover-components-native-20261103/native.json`.
`complete`, `container_absent`, `volume_absent` and
`temporary_test_data_removed` are true. No external database was accepted.

The selection was:

```text
services/learning/runtime/tests/test_queue_epochs_native.py
services/learning/runtime/tests/test_retained_position_migration.py
database/tests/position_study/test_retained_position_store_postgres.py
services/learning/runtime/tests/test_cutover_publication_generation.py
services/learning/runtime/tests/test_families.py::test_tactic_retained_progress_and_original_attempt_replay
```

Reproduction command from repository root:

```sh
uv run --project services/learning/runtime --locked --python 3.12 python -c 'from pathlib import Path; import runpy; runpy.run_path("database/tempo_learning_database/tooling/run_postgres.py")["main"](test_paths=[Path("services/learning/runtime/tests/test_queue_epochs_native.py"),Path("services/learning/runtime/tests/test_retained_position_migration.py"),Path("database/tests/position_study/test_retained_position_store_postgres.py"),Path("services/learning/runtime/tests/test_cutover_publication_generation.py"),"services/learning/runtime/tests/test_families.py::test_tactic_retained_progress_and_original_attempt_replay"], fixture_profile="essential", evidence_directory=Path("/private/tmp/tempo-cutover-components-native-20261103-reproduction"))'
```

The single-position source uses real legacy migrations through 59 and 15
selected complete source table captures, two reviews including one invalidation,
full original FSRS/due, donor, epoch, plan/first-response/grade/receipt references.
It proves repeat transfer/conflict/rollback/collision and unchanged source.
The copied FSRS gives the same next Good response under FSRS 6.3.2, retention
0.92, maximum interval 365 and the same accepted time/day. Historical reviews
were never replayed to regenerate state. Encoded facts remain staging data,
not active review or receipt tables. This is one representative scope, not an
exhaustive source census or accepted target writer.

The new queue regression failed before its fix: a stale worker reordered a newly
protected prefix. It passed after checking current task payload, epoch and source
before ordering. The early shared activation lock was removed to retain the
publication writer's card-before-activation order. Independent Sol source review
found no P0/P1 within the bounded storage/queue mechanism; it did not execute
tests or sign off full migration semantics. Before/after receipts remain under
`/private/tmp/tempo-w03a-preserve-{before,after}-20261103/`.

Additional relevant component evidence is retained without relabeling it as a
newer assembled pass:

- W01 source-removed installed wheel and sdist: 24 tests each; catalog/exchange/
  boundary selection: 22; seven schemas and lock/artifact checks passed.
- W02B installed pure policy: 51 tests; isolated installed-artifact checks: 2;
  fixture-profile checks: 11. Native transactional one-Good remains unproved.
- W03A unchanged focused queue checks: 37; earlier native enqueue cases: 2.
- W02C new pure fixtures: 2; existing pure policy/cohort checks: 47; one native
  case was deselected and is not counted as passed.
- W05A unit: 1; actual disposable Chromium IndexedDB/reload/budget test: 1;
  ESLint passed. Service workers and server reconciliation were not tested.
- W01B focused Node transition/CLI/guard selection: 108, including 8 new
  synthetic guard cases. This does not prove F13.
- P0C Content export: 12; Learning limits: 4; synthetic 7,258 and 10,000 model
  validations. W09A existing native publication/alias cases: 4, plus the new
  10,000-definition native case retained in the combined selection above.

Native component testing used host Python and a disposable PostgreSQL image.
The new runtime service image was **not built or installed**. No assembled
saved-review→authoritative playable-next-card, full browser replay, backup restore
or final migration rehearsal is claimed.

## F01–F14 matrix

Statuses below describe complete fixture acceptance unless scope is explicitly
limited. A partial native/browser pass does not close its full row.

| Fixture | Status | Evidence / unmet requirement |
| --- | --- | --- |
| F01 | NATIVE PASS — representative mechanism | Original selected history/FSRS/due/IDs and matching pinned future review; target serving/accepted-grade port still absent. |
| F02 | BLOCKED | Pure exact-position/credit policy only; native transposition and accepted-grade uniqueness not proved. |
| F03 | BLOCKED | Pure legacy three-clean/failure/split proof; native target recombination/seed owner absent. |
| F04 | NOT IMPLEMENTED | Full interrupted original issued attempt/first-response/save/reconciliation journey needs W03B/C. |
| F05 | NOT IMPLEMENTED | W06A/B wait for accepted-grade/review/queue ports; original accepted Games obligations must remain intact. |
| F06 | BLOCKED | Frozen activation restart is native-tested; full source revision plus protected issued-attempt reconciliation absent. |
| F07 | BLOCKED | Exact original invalidation/history copied; executable target invalidation/receipt semantics not wired. |
| F08 | BLOCKED | Pure day/epoch policy only; authoritative Study-day conversion and native transactional one-Good absent. |
| F09 | BLOCKED | Native progress/original receipt subset; capture, validation, pack/catalog/introduction/rotation transfer absent. |
| F10 | BLOCKED | Real browser passive keys/values/reload proof; late-client authoritative reconciliation/quarantine absent. |
| F11 | BLOCKED | Pure Italian carrier/teaching/source holds; native seed outcomes/donor certificate and executable owner absent. |
| F12 | BLOCKED | Native complete synthetic 10,000 one-move generation; real graph/alias mapping, source-edit race and overflow/paging unproved. |
| F13 | BLOCKED | Source inventory and synthetic binding guard; enforceable old-role/session/CLI/worker denial absent. |
| F14 | NOT IMPLEMENTED | Actual interrupted migration/restore/uncertain target commit/forward repair rehearsal awaits upstream owners. |

## Gates and exact blockers

| Gate | Result |
| --- | --- |
| G0 | PASS: package/DAG/owners/current source verified. |
| G1 | Minimum representative contract PASS; complete live inventory/disposition and schema layout BLOCKED/UNKNOWN. |
| G2 | Initial representative retained-state/next-response mechanism PASS; full transactional position/queue parity BLOCKED. |
| G3 | BLOCKED: accepted-grade/issuance/review, seed/activation, complete Tactics, browser and Games ports missing. |
| G4 | BLOCKED: authenticated native fence and final-watermark/delta/late-client safety not implemented. |
| G5 | BLOCKED: W07 full rehearsal not run because its predecessor acceptance is missing. |
| G6 | This iteration's source/PR/evidence handoff recorded; programme remains open and NO-GO. |

1. **Code / semantic owner:** Learning must define a canonical executable
   position owner and accepted-grade effect+receipt+checkpoint port. W02A is
   deliberately non-serving; W02B computes proposals only. The coordinator and
   W01/W02 owners must settle that contract before W03B/C or W06A/B can safely
   implement issuance, review and accepted Games settlement. No competing
   scheduler or activation of raw staging is an acceptable shortcut.
2. **Code / identity owners:** complete source-to-target seed/donor/held state,
   Tactics capture/pack/catalog, and browser original-key server reconciliation
   are still missing. W04B/W05B were not started ahead of missing server ports.
3. **Fence / safety owner:** database owner must supply an enforceable, bounded
   old-writer fence with in-flight linearization, effective privileges/membership/
   ownership/escalation and stale restart denial, then authenticated final
   watermark/delta verification. A stopped worker, boolean or credential
   rotation is insufficient. Full R-SEM/R-SAFE remain pending downstream proof;
   bounded contract/storage/guard reviews are not their substitute.
4. **Real-state UNKNOWN:** P0A passively observed registered legacy v1 target,
   matching daemon, schema ledger 1–59 and running app image revision labels
   matching main. Games feedback worker was stopped. Deployment receipt
   consistency, modes/cohorts, accepted obligations, complete source scale and
   browser/device authorities remain UNKNOWN. Those facts cannot be guessed.
5. **Qualification:** GitHub Actions was verified disabled; no matching hosted
   checks, merge qualification or merge authority are asserted. The full runtime
   installed qualifier also fails its bundled-database import boundary checker.
   The coordinator reproduced 63 such undeclared imports on unchanged main
   (`/private/tmp/tempo-cutover-baseline-python-boundary.log`); this is a separate
   existing checker blocker, not a waived gate. Component-build owner owns its
   repair. The Node wrapper lacked task-local TypeScript; underlying selected
   Python checks passed. Optional legacy test collection lacked `redis`; pure
   comparator cases passed, not the unavailable legacy runner.
6. **Image / rehearsal:** image build was not run with roughly 3 GiB free disk.
   Qualified runtime image, full saved-review/playable-next, F13/F14 restore and
   browser acceptance remain future delivery work, before any live go/no-go.

Original attempts, reviews, source records, accepted debt and journals were not
changed in the installed product. No deployment, authority switch, downtime,
live privilege mutation, legacy retirement/deletion or backup/volume deletion
was performed. Native fixtures removed only their own identified resources;
reproduction evidence and source branches remain retained for review.
