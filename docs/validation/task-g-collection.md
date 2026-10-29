# Task G collection-first integration

Collection records configured games and honest repertoire deviations while the
actual packaged CLI runs with `--collect-only`. Study delivery/settlement remains
a separate checkpoint. Live collection was never enabled.

## Candidate and proof

The packaged runtime source is `bb7a4879fa217d56fe069d265421d8553d7e07f7`.
Immutable image:
`sha256:8d48934759fec438c732953066e5af739c4ab60dc3443f3cbbbff6ed4231ae80`.
Schema: 54. Later branch changes correct the external acceptance harness,
operator/profile release wiring and the CPU-policy CLI fixture. Every Dockerfile
COPY input remains identical to the packaged runtime source; image identity is
not relabeled to the later branch head.

A–F were integrated from the six reviewed pins, which remained current. Existing
comparison, Settings and resource-policy component evidence is reused where its
source/dependencies are unchanged. This is neither a full-system pass nor an
installed-release claim.

| Exact selected check | Outcome |
| --- | --- |
| `pytest backend/tests/test_product_continuous_sync.py backend/tests/test_product_game_sync_integration.py backend/tests/test_product_feedback_jobs.py -k 'not native' -q` with the recorded scoped Python paths | 132 passed, 4 native cases deliberately outside this selection |
| `test_no_learning_sql_or_study_runtime_dependency` and `test_new_product_boundaries_do_not_import_composition_or_private_siblings` | 2 passed |
| `pytest backend/tests/test_game_collection_status.py -q` | 26 passed, including restricted-reader PostgreSQL |
| `test_collect_only_current_worker_persists_evidence_without_study_effects` | 1 passed on actual PostgreSQL, including unavailable comparison → reconciliation |
| `node tools/component-build/test-lane.mjs runners development-budget` | 94 passed, no skips |
| `node tools/component-build/test-lane.mjs runners game-review-collection` | 10 passed, no skips |
| `TEMPO_COLLECTION_TEST_IMAGE=<immutable image above> TEMPO_COMPONENT_PYTHON=<scoped interpreter wrapper> node tools/component-build/test-lane.mjs --suite games-collection-release` | 1 packaged acceptance passed, no skips; 42.41 seconds in the recorded registered run |
| Whole `runners tempo-cli.test` lane | Initially 193 passed / 18 failed; after correcting the Task A fake-builder metadata, the exact 18-case recheck had 15 passed / 3 existing failures |
| Selected Games suite manifest, inactive Compose resolution, AST/Node syntax, conflict and whitespace checks | Passed |

The CLI fixture now derives memory and CPU kernel facts from the actual requested
builder options; it no longer invents the retired two-CPU quota. Production
resource guards remain intact. The three remaining CLI failures reproduce on the
exact reviewed base: [#608](https://github.com/aaweaver-actuary/tempo/issues/608).
The full manifest checker also has an independently reproduced base failure for
an existing relocated database input: [#607](https://github.com/aaweaver-actuary/tempo/issues/607).
Selected-manifest success does not waive that global failure or hosted gates.

## Single successful packaged session

The registered acceptance uses the existing disposable PostgreSQL fixture, ordered
real migrations, the original role-provisioning owner, and the actual CLI/reader
inside one pinned image. Only synthetic provider HTTP, clocks and quiet admission
are supplied. No production source overlay or successful finding/receipt is seeded.
Deployment resource isolation is not established by the synthetic admission input.

- 410 games per provider cover Lichess pagination and dense September/October
  Chess.com archives. Two delayed-visible games bring the final unique total to
  822, with zero unresolved comparisons after reconciliation.
- Both selected repertoire colors participate. An initially absent Black source
  becomes available; real reconciliation revisits it. Opponent-first departures
  and authored own misses without a physical target remain distinct.
- The reader reports 550 unique own-response misses and 276 pending original
  commands, with no acknowledged deliveries. One synthetic pre-existing uncertain
  delivery has three attempts; its complete stored row remains unchanged.
- Repeated material rescans retain identical capture keys and command rows.
  Manual interleaving never resets forward coverage; forward and historical
  reconciliation retain distinct frozen windows across fresh packaged processes.
- Complete serialized rows in cards, reviews, daily queue, queue-attempt origins,
  Study attempts, Study feedback and review receipts remain unchanged during
  collection. The protected ordinary issued attempt saves successfully afterward.
- Actual adapter-to-coordinator regressions separately establish explicit
  unfinished versus missing/unsupported-result checkpoint behavior and later
  finished-game reconciliation. The unchanged runtime inputs match the image.

Initial harness failures (image-ID selection, serialized row comparison and
case-sensitive Docker absence text) are retained outside Git. The assertions
were corrected without weakening the state/cleanup requirements. Exact fixture
cleanup is verified; no other task's existing Study environment was reused or stopped.

## Inactive installation and remaining blockers

Actual installed source remains `571d80a5e787b380b94c40f2c3e98fa6cdf64e53`, schema
54, with retained optional images from a different revision. Its frozen receipt
contains no collector image/service, and its configuration fingerprint does not
match the newly inspected resolved configuration. No installation was attempted
through bypasses; the candidate is unmerged and required qualification is not green.

Fresh private reader evidence confirms both new collection configurations absent,
zero collection games and 2,178 separate retained legacy games. Missing inputs
remain account binding, seed window/precision, repertoire IDs, speeds, rated
policy, recurrence, overlap and reconciliation cadence. Legacy saved accounts are
preserved separately; selected-repertoire compatibility is not inferred.

Installed host/container inventory found no running provider collector. Existing
legacy workers/history capabilities and Study feedback maintenance remain in
place. Inspection of the installed source identifies the latter as resuming
already persisted Study effects, rather than draining the product outbox. Global
Study-delivery disablement remains unknown; the operator rechecks recognizable
competing runners before enable. Arbitrary unmanaged launchers are outside its
shared-lock guarantee.

The live fractional CPU limits remain unchanged (including background worker
0.35, scheduler/backup 0.05, defense 0.25 and Maia 0.30 CPU). New managed releases
freeze/build the optional profile, stop an enabled collector before protected
maintenance, and never automatically start it. Missing launch arguments resolve
empty for inactive packaging; the operator rejects them before startup. A future
protected release must verify exact images/configuration and effective CPU,
memory and PID settings after controlled recreation. PostgreSQL must not be
recreated merely to remove a quota.

Remaining: resolve actual required-check blockers and obtain authorized main
integration, install the qualified source through the managed release transition
with collection OFF, supply explicit launch inputs, then repeat the read-only
installed identity/configuration/producer checks. Andy's separate switch-on
instruction remains required. No #594/#595 settlement, ranking, engine, coaching
or UI expansion is included.

See the [operator commands and canonical lifecycle drawing](../../scripts/README.md#games-collection-operator).
Private launch values/provenance remain outside Git; public evidence contains
only synthetic game data and operational identities, never accounts or credentials.
