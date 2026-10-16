# Independent Analysis Workbench

Narrow Analysis UI, with its own dependencies and static build. No Study source,
store, queue client, engine/model runtime, Python or database is required.
Connect explicitly to Intelligence using its caller token. Session storage retains
that token; URLs, exports and durable command documents do not. Configure the exact
browser origin in `TEMPO_INTELLIGENCE_ORIGINS`. No API proxy or live fallback exists.

```sh
npm ci --prefix apps/workbench-web --workspaces=false
npm test --prefix apps/workbench-web
node apps/workbench-web/tests/browser.mjs /absolute/native/workbench-producer.json /absolute/evidence/browser
node apps/workbench-web/qualify.mjs /absolute/evidence/restricted
```

The native fixture is produced by Intelligence's PostgreSQL runner, not hand-authored
UI recommendations. Its games are explicitly synthetic. Native tests use a private
ephemeral static host, strict API interception, real storage/reload, desktop/phone
boards and declared traffic. Restricted qualification packages the static build,
removes source, installs the tarball and repeats public-client/native browser proof.
No offline/service-worker claim is made. Piece SVGs reuse Tempo's Merida assets;
Chessground base CSS is copied unchanged from its pinned 10.1.1 package.

Use the existing F2 `TEMPO_WORKBENCH_URL` launch setting to link this independent
static origin. No Study source change or credential handoff is required.
