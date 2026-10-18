# Selected opening rebuild maintenance

This is a development prerequisite and a **future procedure requiring separate
execution approval**. It authorizes no live run, deploy, merge, migration,
settings change, content repair, general worker exception, or new credential.

## Operator contract

`scripts/recover-opening-publication.py --mode queued-rebuild` defaults to
read-only preflight plus private filesystem evidence. Initial pins are exact
database target hash, repertoire/task ID, generation, source/prefix revisions,
and New York study day. The existing task must be `queued`/`queued`, zero attempts,
with an older ready publication and no target-or-newer staging. Depth,
Study-only, frozen activation and expected follow-up task generations are saved.
Active prefix publication/recovery is refused.

Apply claims only that graph row and calls existing bounded phase handlers.
Only its owner-requested matching integrity scan and selected segmentation run
follow; follow-up IDs are saved before claiming. Normal worker allowlists stay
unchanged. Generation and 60-second exact-row leases, graph batch sizes, and
ordinary transaction/lock budgets remain. Do not raise timeout overrides.

Each claim and owner write checks the pins. Source/policy locks hold to commit,
and checkpoint/completion repeat the scope check. Existing migration 35 triggers
exclude generated provenance from authored source revisions; a slice advancing
the pinned authoritative source rolls back. Protected card reclassification or
cleanup of an issued attempt/today's queued head stops before that mutation.

Resume requires the same saved plan/pins and its durable task scope marker.
Only that plan's expired lease may be reclaimed. Current leases, replacement
generations, changed source/day/target, future retry cooldowns and terminal
failures refuse continuation. Never clear leases, reset phases, edit a plan's
day or use SQL to mark readiness. The failed-first-publication mode stays separate.

The integrity owner's **32 issue / 64 card-source-block caps remain unchanged**.
Exceeding either stops with the ordinary diagnostic. Completion can truthfully
report `needs_repair`; it does not promise clean content or an introduction.
Success requires the matching published scan and ready segmentation run, plus
preservation checks. Each transaction rolls back on failure; earlier committed
slices remain. There is no globally reversible multi-slice write.

Integrity completion requests the **shared ordinary daily queue refresh**; this
operator never claims it. Ordinary Study-only queue work can later change other
unissued entries. Normal protected-head, issued-attempt, review/history,
schedule and frozen-order protections retain their ordinary owners. Verify the
actual queue separately; operator completion alone is not queue readiness.

## Finite qualification still required

The regular owning unit suites cover exact claims, source/day/identity and
generation guards, interruption/reclaim, stale deliveries, checkpoint scope
postconditions, protected data, publication caps and CLI dry-run/resume pins.
The existing registered native owner is
`scripts/check_postgres_opening_graph_recovery.py`. Added cases cover retained
gen1/queued gen2, new generated cards, matching scan/segmentation, committed
checkpoint restart, exact lease abandonment/reclaim, generation/source/day and
unrelated-task refusal, real transaction rollback, protected schedule/history/
issued-head/frozen evidence, and the queued shared refresh. Expiry is simulated
in the disposable fixture; this is not an OS-kill rehearsal.

Obtain the parent's finite resource window before native work. Proposed cap:
**10 minutes, 1.8 CPUs / 1920 MiB total**, with three unique owned containers:
PostgreSQL 18.6-trixie (0.7 CPU / 768 MiB), Redis 7.4-alpine (0.1 CPU / 128 MiB),
and a prebuilt backend helper (1 CPU / 1024 MiB). Use an isolated network,
aliases `postgres`/`redis`, no host ports, no study volumes/credentials. Record
container/image IDs, owner labels, inspected limits, network, exit code and log.
Mount the exact feature revision read-only at `/source`; in the helper run:

```sh
TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://redis:6379/0 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/source/backend:/source/packages/scheduling/src:/source/packages/chess-core/src:/source/services/learning/application/src:/source/services/learning/runtime/src:/source/services/learning/remediation/src:/source/contracts/game-evidence/src:/source/contracts/content-publication/src \
/usr/bin/timeout --signal=TERM --kill-after=10s 540s \
python /source/scripts/check_postgres_opening_graph_recovery.py
```

Use a prebuilt qualified image; no build, engines, browser or full-system runner.
The script's `postgres@postgres` admin connection must reach only this isolated
fixture (private-network trust authentication is acceptable only there). It owns
one unique temporary database. Reserve 60 seconds for verified owned cleanup.
Do not substitute the host `--disposable` shortcut without resource fencing.

Also qualify the four-file overlay below against the exact recorded live backend
image in disposable PostgreSQL. Main `67a11752` includes queue changes absent
from live `72530cfa`; main-based tests do not prove that compatibility. Each
native/compatibility pass needs its own approved finite allocation. Missing
native/compatibility runs are unqualified; source-level passes are not substitutes.

For that compatibility pass, mount only `scripts/`, `backend/migrations/` and
`backend/app/schema_version.py` as fixture inputs under `/source`, plus the
four-file overlay under `/app` and `/operator` as below. Set `PYTHONPATH=/app`;
do not mount `/source/backend/app` or use the main-based source-path list, which
would silently select newer app owners. The schema-version file imports no app
runtime. Keep all database connections on the disposable network. After the
separate bridge PR311 changes integrity/task owners or schema, requalify that
actual combination; these branches have not been integration-qualified together.

## Future 30-minute downtime window

Verify a backup **before** the window. Record the registered Compose target,
exact running app container IDs/image IDs, database identity/schema, source/task
pins, existing secret mounts and network. Verify helper `/usr/bin/timeout` and
dependencies before downtime. Do not pull/build images during the window.

The cap is 30 minutes from app shutdown through verification/restart, not a
promise of rebuild completion. Reserve at least ten minutes for evidence and
same-version restart; the operator tree gets at most twenty. PostgreSQL, Redis
and the retained private viewer stay up. Engines/scheduler already stopped stay
stopped. Record as Bash array `APP_IDS` only the previously running `api`,
`foreground-worker`, `background-worker`, `web` container IDs. Restart these
same IDs, avoiding recreation, image selection, migrations and deployment.

Populate verified values in a clean Bash shell: `FEATURE` (exact feature source),
`PRIVATE` (0700 evidence directory), `BACKEND_IMAGE` (immutable live backend image
ID), `WRITER_ID` (existing foreground-worker container), `APPROVED_NETWORK`,
`TARGET`, `REPERTOIRE`, `TASK`, `GENERATION`, `SOURCE_REVISION`, `PREFIX_REVISION`,
`DAY` (New York). Dated metadata was source 197/prefix 0, queued gen2, retained
gen1, schema49, Study-only true. Reverify it and resolve the missing task/
repertoire IDs and target hash read-only before execution approval; do not guess.

The helper uses the existing backend image and secret mounts, overlaying only
this change's three service files and CLI. Verify those owner baselines match
the recorded live image; do not overlay the rest of newer main. Inspect
`WRITER_ID` mounts before `--volumes-from`; stop if they include study data or
unrelated mounts. No new credentials/access files are created.

```bash
umask 077
mkdir -p "$PRIVATE"
chmod 700 "$PRIVATE"
pins=(--mode queued-rebuild --target "$TARGET" --repertoire-id "$REPERTOIRE"
      --task-id "$TASK" --generation "$GENERATION" --source-revision "$SOURCE_REVISION"
      --prefix-revision "$PREFIX_REVISION" --day "$DAY" --output /evidence/rebuild)
run_operator() {
  docker run --name "$OPERATOR_NAME" --label tempo.owner=selected-opening-rebuild \
    --init --read-only --cpus=0.75 --memory=768m --network "$APPROVED_NETWORK" \
    --volumes-from "$WRITER_ID:ro" --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app -e TZ=America/New_York \
    -e TEMPO_DATABASE_READ_URL=postgresql://tempo_writer@postgres:5432/tempo \
    -e TEMPO_DATABASE_WRITE_URL=postgresql://tempo_writer@postgres:5432/tempo \
    -e TEMPO_REDIS_URL=redis://redis:6379/0 -e PGPASSFILE=/run/secrets/writer_pgpass \
    --mount "type=bind,src=$FEATURE/backend/app/services/opening_graph_recovery.py,dst=/app/app/services/opening_graph_recovery.py,readonly" \
    --mount "type=bind,src=$FEATURE/backend/app/services/durable_tasks.py,dst=/app/app/services/durable_tasks.py,readonly" \
    --mount "type=bind,src=$FEATURE/backend/app/services/postgres_opening_graph.py,dst=/app/app/services/postgres_opening_graph.py,readonly" \
    --mount "type=bind,src=$FEATURE/scripts/recover-opening-publication.py,dst=/operator/scripts/recover-opening-publication.py,readonly" \
    --mount "type=bind,src=$PRIVATE,dst=/evidence" --workdir /app \
    --entrypoint /usr/bin/timeout "$BACKEND_IMAGE" \
    --signal=TERM --kill-after=10s 1200s python /operator/scripts/recover-opening-publication.py \
    "${pins[@]}" --max-seconds 1100 --max-slices 20000 "$@"
}
# Preflight only; review preflight.json and before.json.
OPERATOR_NAME="tempo-selected-preflight-$(date -u +%Y%m%dT%H%M%SZ)"
run_operator
```

Save preflight log/inspect/exit status; verify its owner label before removing
that stopped helper. If still-unstarted pins change, preserve the evidence and
use a fresh preflight directory. At the separately approved window start,
record UTC start/deadline, then:

```bash
docker stop --time 15 "${APP_IDS[@]}"
OPERATOR_NAME="tempo-selected-apply-$(date -u +%Y%m%dT%H%M%SZ)"
run_operator --resume --apply
```

In a second terminal, stop only the verified owned tree with these exact commands:

```bash
if test "$(docker inspect -f '{{index .Config.Labels "tempo.owner"}}' "$OPERATOR_NAME")" = selected-opening-rebuild; then
  docker stop --time 10 "$OPERATOR_NAME"
  docker logs "$OPERATOR_NAME" > "$PRIVATE/$OPERATOR_NAME.log" 2>&1
  docker inspect "$OPERATOR_NAME" > "$PRIVATE/$OPERATOR_NAME.inspect.json"
else
  printf '%s\n' 'Owner mismatch; do not stop this container.' >&2
fi
```

SIGTERM requests a stop between slices. Outer timeout/container stop also bound
evidence capture and stalled computation. A killed transaction rolls back; a
claim may retain its lease until expiry. Never use global Docker stop/prune,
process-name matching, lease resets or SQL readiness changes.

For an approved resume in the **same day and original window**, prove the old
container/process exited, compare its exact lease log with authoritative task
lease expiry, reuse `pins`/plan/baseline, choose a fresh `OPERATOR_NAME`, then
`run_operator --resume --apply`. Lower both helper timeout and `--max-seconds`
to the remaining original window with restart reserve; do not automatically
grant another twenty minutes. A current lease means wait for its recorded
expiry only after proving the owner stopped. Changed day/source/generation or
terminal failure needs diagnosis and a separately reviewed plan.

Before the deadline preserve logs, inspect/exit code, per-attempt leases,
after/error/result and preservation files. Verify exact graph task/publication,
matching scan/status and segmentation run, Study-only true, all preservation
checks, and no active owned helper/lease. Then restart the same IDs, whether the
operator completed or stopped:

```bash
docker start "${APP_IDS[@]}"
```

Compare IDs/images with the initial record, check API/web health and actual
ordinary Study queue recovery on the same version. Verify protected queue,
issued/history/schedules/frozen evidence again after the shared refresh. Report
`needs_repair` or queue failure honestly; do not repair it here. Preserve backups
and checkpoints. Remove only stopped, label-verified helper containers after
saving their evidence, and verified owned qualification resources; never study
volumes or another task's data.
