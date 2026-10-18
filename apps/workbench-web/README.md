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

PR295 recovery uses the exported small `PendingCommands` coordinator. It keeps one
immutable serialized request per exact origin/workspace in session storage,
persists before dispatch, and blocks new commands until authoritative receipt
recovery or identical retry succeeds. Receipts must match workspace, command ID
and request digest. Lost responses, missing receipts and authentication failures
retain pending intent. Legacy v1 pending records migrate without credentials;
conflicting/malformed records remain retained and block transmission. The public
client's `requestSerialized` method preserves saved request bytes and its existing
connection-generation fences. Storage failure prevents transmission.

Run creation uses that same mutation slot and fence. `submitRun` generates one
run ID, retains exact POST bytes and a path/body digest in a credential-free v3
`run_create` record, and verifies persistence before dispatch. `recoverRun` reads
`GET /v1/analysis/runs/{run_id}` and compares its complete immutable request scope,
including normalized defaults. A matching status resolves creation without
another POST; 404, rejected authentication, lost responses and scope conflicts
retain the original intent. `retryRun` replays identical bytes and the original
run ID, then confirms through GET. The existing authority storage key is retained;
v2 commands and v1 migration remain supported. Commands and creation block each
other until resolution. Reload recovery navigates only after confirmation, and
switching origin/workspace retains the original record for reconnection.

Unknown engine repertoire/lineage is displayed explicitly. Old report generations
remain readable with a legacy attribution notice. A report or response draft does
not approve, publish, activate or admit training content.

“Browse selected games” reads the accepted input even when a game has no positive
finding. It pages through the exact selected scope, renders its full legal move
order and original comparison status/reason at each learner position, and retains
explicit unknowns. It uses only Intelligence's authenticated read endpoint;
provider links are supplied provenance, never guessed IDs. The native producer
fixture now includes the actual games read response for browser qualification.
