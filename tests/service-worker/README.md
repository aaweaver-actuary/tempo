# Service-worker upgrade and retained review

`upgrade-review.spec.ts` is a narrowly scoped Chromium proof using a fresh
Playwright context and an ephemeral loopback static server. Set
`TEMPO_SW_CANDIDATE_ARTIFACT` to a task-owned local frontend build from the
candidate under test and `TEMPO_TEST_OUTPUT_DIR` to task-owned evidence. The
server serves the emitted worker twice, changing only its cache version in the
second response. It never uses an installed browser profile or live Tempo API.
The standard phone/offline suite and full PostgreSQL assembly remain separate.

This source is a planned proof; no browser, build, upgrade or replay acceptance
is claimed until the exact candidate artifact and case are run.
