# Explicit one-game v3 producer and operating path

Implementation phase started **2026-10-18 20:59:39 UTC**, with a22:10 checkpoint
target and22:25 hard rescope boundary. This is the initial one-game slice on
`codex/legacy-game-study-bridge`, based on `67a11752097147816fc8090caa10bb821265eb4e`.
Its exact final head and checkpoint time are recorded in the task evidence and
PR. No live capture, delivery, queueing, review or resource launch occurred here.

## Ownership and transport

1. Backend `app.legacy_game_observation.capture_game` exports one explicitly
   identified completed imported game and one selected repertoire. It uses the
   existing bounded source owner and complete catalogue/policy-prefix proof,
   before/after authority observations and the original physical executable.
   Default scope includes every learner decision, up to128. No silent sampling.
2. A bridge `observe_capture` validates the capture digest and reuses its existing
   exact physical-card/interior-decision projection. This file-only adapter
   imports contracts, not backend or GE implementations. The canonical source
   row digest is distinguished from unavailable original provider PGN bytes.
3. GE `preview_legacy_observation` replays legal moves through the shared
   comparison/frontier owner and constructs a real v3 envelope. The existing v2
   builder is unchanged. Unknown/no-miss produces no envelope; multiple physical
   miss targets are explicitly unsupported in this first slice. No new card,
   engine, provider access, publication or activation receipt is introduced.
4. A bridge `LegacyStudyOperator` makes explicit loopback HTTP intents only.
   Accept uses the existing HTTP → Celery `game_evidence.receive_legacy` → durable
   operation receipt boundary. Receiver preparation independently recaptures the
   exact miss encounters outside its transaction; acceptance rechecks source,
   graph, physical card and catalogue authority under existing owner locks.
5. Queue is a separate foreground command, `game_evidence.admit_legacy`, through
   the normal prelocked writer and shared admission. Card/day/remediation lock
   order, protected heads, frozen activation, quotas and unrelated histories
   retain their existing owners. Inspection and preview never issue a review.

Study-only mode admits only the exact explicit `game_evidence.receive_legacy`
background command. It is **not** added to `_CONTROL_BACKGROUND_COMMANDS`:
foreground admission and capture's bounded yield checks still apply. No ordinary
analysis/graph/provider task kind is enabled; `STUDY_TASK_KINDS` remains
`('daily_queue',)`. Receipt bookkeeping keeps its existing short control section.

The unchanged12-table installable remediation module remains authoritative for
accepted incidents, admission, first-issuance frontier binding and qualifying
review settlement. Accepted game/repertoire scopes stay disjoint from old weak
legacy misses, including after withdrawal. A previous/offline issued attempt
cannot acquire a later frontier; normal/reconciled review and invalidation use
the shared settlement rules. No second scheduler or history migration is added.

## Procedure after qualification and separate real-action authorization

These commands describe the installed path; they were not run against a live
backend. Prerequisites include backend migration50, the existing backend read
role configuration, a completed current graph/integrity certificate, reviewed
qualified artifacts and the existing command worker/operation-receipt service.
No new credentials, provider activation or service reconfiguration is implied.

Choose exactly one completed game ID and one current repertoire ID. Use new
output paths and retain these immutable files. From the repository root in the
configured backend environment:

```sh
PYTHONPATH=backend python -m app.legacy_game_observation \
  --repertoire-id SELECTED_REPERTOIRE_ID --game-id SELECTED_COMPLETED_GAME_ID \
  --output one-game.capture.json
```

Then use the installed A bridge and GE tools offline:

```sh
tempo-a-legacy-observe --capture one-game.capture.json --output one-game.observation.json
tempo-game-evidence legacy-preview --observation one-game.observation.json --output one-game.preview.json
```

Inspect `result`, `findings`, actual target/workspace/repertoire/card identity,
played/expected moves and interior exercise offset. An unknown/no-miss or
unsupported result ends this selected game without admitting anything. Both
file tools cap inputs at8MiB. Source capture retains its existing larger bounded
traversal budget; an oversized resulting file is an explicit unsupported boundary.

With the separately authorized actual API origin, inspect before accepting:

```sh
tempo-a-legacy-study --endpoint http://127.0.0.1:8000 status \
  --preview one-game.preview.json --output one-game.status-before.json
tempo-a-legacy-study --endpoint http://127.0.0.1:8000 accept \
  --preview one-game.preview.json --output one-game.accept.json
tempo-a-legacy-study --endpoint http://127.0.0.1:8000 receipt \
  --preview one-game.preview.json --output one-game.receipt.json
```

Acceptance is true only for a completed typed acknowledgement matching the
original delivery/evidence/revision and exact obligation IDs. Queued, retrying,
blocked or unknown transport state is not acceptance. The tool performs one
request per explicit action; it does not poll or drain. Foreground activity can
defer intake without discarding its saved durable envelope.

If a response is lost, keep the original preview. Explicitly read its receipt;
if no receipt exists, repeat accept with that same preview and a new output
filename. The deterministic accept operation and immutable delivery stay the
same. Do not recapture, hand-edit JSON, infer a correction revision or manufacture
another obligation. Export deliberately refuses games already in accepted module
lineage. Corrections/withdrawal production are outside this initial file operator.

Only after successful acceptance, explicitly request queue admission:

```sh
tempo-a-legacy-study --endpoint http://127.0.0.1:8000 queue \
  --preview one-game.preview.json --operation-id ONE_GAME_QUEUE_REQUEST_1 \
  --output one-game.queue.json
tempo-a-legacy-study --endpoint http://127.0.0.1:8000 status \
  --preview one-game.preview.json --output one-game.status-after.json
```

Queue first verifies that the original acceptance receipt completed and that the
actual target/card revision is still current. It reports applied/deferred/rejected
or a pending operation. A pending queue operation can be read at the existing
GET `/api/operations/ONE_GAME_QUEUE_REQUEST_1`; replay that same queue key after
response loss. A later intentional retry after a **completed deferred** result
uses a new key, since a completed operation is immutable. No automatic drain is
needed. A protected head stays first; status reports that the target waits behind
it. An already issued target or burial can require a genuinely fresh later
attempt. The tool never completes the protected card or submits a review.

The user later reviews through normal Study. The first actual issuance binds the
accepted frontier atomically before the origin stamp. Read-only status separates
accepted, current/stale, actual queued/deferred state and unsettled/settled state.
No real qualifying-review claim can be made from this source fixture.

## Executable synthetic checkpoint and limits

Run the explicit cross-component source fixture with the relevant source paths
in a pre-existing backend test environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=contracts/game-evidence/src:services/learning/remediation/src:services/game-evidence/src:tools/analysis-restoration/a_bridge/src:backend:backend/tests \
python -m pytest -c backend/pyproject.toml \
  tools/analysis-restoration/a_bridge/integration/test_legacy_pipeline.py \
  -q -p no:cacheprovider --tb=short --maxfail=1
```

The two cases generate the one-game v3 envelope from read-only owner capture,
independently prepare it again at the receiver, execute shared accept/replay,
explicitly queue behind a protected head and exercise actual SQLite Study
issuance/review writes. Fresh issuance yields one binding/review/settlement,
review replay stays exactly once and real review invalidation restores the
obligation. The paired prior-issued origin yields no retrospective binding or
settlement. No hand-edited delivery fixture substitutes for producer output.

Module PostgreSQL rows and HTTP/Celery/operation transport are scripted. This
does not prove native transaction rollback, concurrent locks, worker restart,
installed package entrypoints, backend SQL query plans or a real user's review.
The new proposed artifact inventory is284 cases, unexecuted and requiring fresh
review/grant; the old consumed255-case attempt remains a startup failure with
zero artifact/test results. Five module-native cases alone cannot qualify the
backend path. Workbench polish and automatic draining remain out of scope.
