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


## Connected navigation

`TEMPO_STUDY_URL` is an optional, public **build-time** setting for the general
Open Study link. It must be an absolute HTTP(S) root URL, without userinfo, path,
query or fragment. For example:

```sh
TEMPO_STUDY_URL=http://127.0.0.1:3000/ TEMPO_INTELLIGENCE_ORIGIN=http://127.0.0.1:8149 npm run build --prefix apps/workbench-web
```

The owning Vite config rejects unsafe settings before emitting assets and does
not echo the rejected value. The public `mountWorkbench(root, {studyUrl})` API
applies the same validation. With no setting, no Study link appears. A finding
link opens the normal Study destination and explicitly leaves its physical-card
identity unverified. A gap does not imply an existing Study card. No card-specific
URL, admission, review, Content operation or credential transfer occurs.

The full local app can launch this independent app using its existing public
`TEMPO_WORKBENCH_URL` build setting. Supported query keys are only `run`,
`category` and `dismissal`; userinfo, fragments and non-HTTP(S) schemes are rejected
before bundling. Neither setting is a runtime switch for an already built app.

The Intelligence origin is an API address to enter in the connection form, not
the Workbench page to navigate to. Connection credentials remain in the existing
per-tab session storage: reload or return to that connected tab to stay connected;
a fresh tab needs Connect. No localStorage credential, proxy, cross-window
handoff or persistent credential is added.

The owning qualifier builds with the non-secret fixture Study URL
`https://study.example.test/`; the browser intercepts this target locally and
proves a general root GET with no Authorization, referrer, card query or API
mutation. For a direct native browser run, first build using that fixture setting.
The qualifier also executes the actual public Vite build-config rejection checks
before bundling, then verifies the source-removed package as before.

`TEMPO_INTELLIGENCE_ORIGIN` is a separate optional public build setting. It must
be a root HTTP(S) origin with no userinfo, path, query or fragment. It prefills the
connection form only; it never auto-connects or carries a token. Without it, the
origin field is empty rather than guessing a port. An existing per-tab connection
continues to use its saved origin. The owning build boundary rejects unsafe values
before embedding them. The qualifier uses `https://intelligence.example.test` as
its explicit non-secret fixture form default, then fills the intercepted test API
through the normal connection UI.
