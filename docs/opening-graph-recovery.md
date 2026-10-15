# Recover a failed first opening publication

A staged graph is invisible to introduction until publication completes. A failed
`opening_graph_rebuild` at `publish` can therefore leave practiced-parent children
locked indefinitely, even with active frozen Maia ranks. Study-only mode deliberately
admits only daily-queue tasks, so generic Retry alone cannot repair this state.

`scripts/recover-opening-publication.py` is explicit single-repertoire maintenance.
It requires task ID, repertoire ID, generation and the exact database target hash
used by the frozen-order utility. It validates every staged occurrence against
current source lines, training depth and prefix overrides, then validates existing
card content, kinds and exact repertoire membership. It refuses an existing graph
publication, changed source, missing links or a task outside failed `publish`.

Use configured reader/writer connections and Redis admission. Start with a fresh,
private output directory; captures include study history. Without `--apply`, the
command captures and validates only:

```sh
backend/.venv/bin/python scripts/recover-opening-publication.py \
  --task-id TASK --repertoire-id REPERTOIRE --generation GENERATION \
  --target TARGET --output PRIVATE_OUTPUT
```

Add `--resume --apply` after reviewing that capture, or use `--apply` on the initial
invocation. Recovery claims only the exact task row, preserving its phase and
issuing a new lease. Existing graph handlers publish, classify, check integrity,
clean up and finalize; only that graph's resulting integrity task may follow.
Its local study day is updated to the current New York day so completion requests
today's ordinary queue refresh. Source revision, study-only mode and frozen
activation must remain unchanged. Background database sections retain normal
transaction limits and yield to foreground activity. Ordinary workers remain paused.

The command never enables analysis, changes quotas or probabilities, or issues a
study attempt. Recovery completion is not proof that the daily queue is ready:
inspect the projection and actual admissions afterward, including current frozen
fingerprints and preservation of existing reviews and issued attempts.

After interruption, `--resume --apply` revalidates the saved scope and current
graph before continuing saved checkpoints. It refuses a currently leased task;
never reset another runner's lease. Terminal failures beyond the initial publish
checkpoint need diagnosis, not automatic phase reset.

Focused checks:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_opening_graph_recovery.py backend/tests/test_frozen_opening_order.py backend/tests/test_postgres_durable_phase.py backend/tests/test_opening_progression.py -q --rootdir=.
PYTHONPATH=backend backend/.venv/bin/python scripts/check_postgres_opening_graph_recovery.py --disposable
```

The native proof is also registered in the regular PostgreSQL durability suite.
No schema, public API, unlock-policy or normal scheduling change is required.
