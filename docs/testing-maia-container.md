# Maia qualification in the pinned Node container

Use this optional local recipe when the host Node version differs from Maia's
declared version. It runs the existing pure/model suites and restricted installed
artifact qualifier unchanged. It neither runs the legacy supervisor nor starts
an API or database. Source qualification and deployment remain separate claims.

```sh
python3 scripts/qualify-maia-container.py \
  --source /absolute/task-owned/disposable-tempo-checkout \
  --evidence /absolute/new-evidence-directory
```

Use installed Docker and Python's standard library; no host Node download is
needed. The source checkout must be clean and contain no host `node_modules`.
The recipe archives its recorded Git revision into a fresh temporary snapshot;
`.git`, ignored credentials and caller working-tree files are never mounted.
Only `workers/maia` lockfile dependencies are installed in Linux. The snapshot
mount is writable for dependencies and normal model/schema staging. Use a
task-owned checkout; evidence must be outside it.

The image is the official digest-pinned Node reference already in the source
`Dockerfile.maia`. The report retains its digest, local image ID, Linux arm64
identity and actual Node version. The current pin is Node 26.10.0.

One container is limited to 2 CPUs, 2 GiB memory with no extra swap, and 256 PIDs.
Both the host and container bound execution to 30 minutes, with a separate
five-minute host cleanup budget. The image's existing `timeout` executable covers
installation, the network barrier and tests (TERM, then KILL after ten seconds);
the recipe fails rather than installing missing tools. A lost host cannot leave
the container running indefinitely. A later run refuses to start while any prior
labelled Maia recipe container still needs ownership reconciliation.
It acquires `/tmp/tempo-mac-validation-20261025.lock` without truncation; a busy
lock fails before Docker work. It also checks daemon capacity and observed
headroom, refusing to expand the limits. The report is not a host-isolation SLA.

The only mounts are the task's tracked source snapshot, evidence and temporary
npm cache. The image filesystem is read-only; there are no ports, host networking,
privileged mode, credentials, home-directory mounts or Docker socket mounts.
Network access is used for the locked dependency install, then disconnected and
inspected before the existing tests and offline restricted qualifier execute.

Cleanup inspects the exact container's ownership label before removing it,
confirms absence, then removes only its temporary cache and source snapshot. Reports and packaged
artifacts remain. The official reusable image is retained; no prune runs. On an
uncertain cleanup result, preserve the report/cache and reconcile only its
recorded container identity. The caller owns checkout cleanup after evidence
and unique work have been preserved.
