# Workbench owning regressions

`tests/client.test.mjs` via `npm test` owns authenticated origin/workspace delivery,
foreign-response rejection, stale connection/JSON-decoding fencing, capability and policy validation, and explicit auth failure.
`tests/browser.mjs` owns `C_native_workbench`: strict declared traffic, native
connection/filter/detail/board controls, pending dismissal receipts, a lost POST response and authoritative receipt/state recovery after real reload, and phone bounds.
The explicit producer JSON must come from the native Intelligence runner. Missing
fixture/browser/build and unexpected traffic fail. No backend or live API fallback.
`qualify.mjs` owns source-removed installed public-client and native browser proof.

PR295: `tests/client.test.mjs` additionally owns the named R4 unresolved-overwrite,
exact byte retry, reload/receipt/missing-receipt, workspace/origin partition,
authentication/receipt mismatch, storage failure, credential-free legacy migration,
and continue/retry/cancel/resume recovery cases. `tests/browser.mjs` exercises
second-command blocking while dismissal outcome is unresolved, lost response,
reload, authoritative receipt recovery, actual reopening/reload, and an actual
C-produced null-lineage engine finding with board rendering/dismissal/reload.

The native browser scenario also verifies legacy run controls omit Continue,
Retry and Resume, retain Cancel, and explain revision recovery. These predictable
rejections must never create an unrecoverable pending command.

R7 owning client regressions in `tests/client.test.mjs`:

- `R7 run creation survives response loss with original identity`
- `R7 repeated Start analysis cannot create a second run`
- `R7 missing run status preserves original creation`
- `R7 conflicting run identity fails closed`
- `R7 run creation and command mutations share authority gate`
- `R7 origin and workspace switching preserve pending creation`
- `R7 storage failure prevents run creation transmission`
- `R7 authentication rejection retains creation evidence`
- `R7 malformed or changed pending creation retains evidence and sends nothing`
- `R7 disconnected response cannot clear pending creation or another authority`
- `R7 original v2 command records remain recoverable`
- `R7 incomplete authoritative scope cannot borrow client request defaults`

`tests/browser.mjs` invokes the strict real-browser scenarios in
`tests/run-creation.mjs`: `R7 browser response-loss`, `R7 browser repeated-click`,
and `R7 browser authority-switch`. They prove one logical run, original input and
budget, real storage/reload recovery, retained 404 uncertainty, and origin/workspace
isolation. Unexpected service traffic fails. These cases also run from the
source-removed package qualifier and registered Workbench component lane.

C3 game inspection: `tests/client.test.mjs` owns supplied source URL validation and
legal full-history frames (including exact en-passant FEN and custom full-move
numbers). `tests/browser.mjs` additionally requires the native producer's accepted
games response, rejects a wrong input identity before board rendering, inspects
an unknown comparison through its actual move order, checks desktop/phone bounds
and asserts zero mutations from game browsing. Source-only checks do not establish
this browser proof; the actual native producer fixture must be regenerated.

`C3_retrospective_repertoire_scope_visible`, asserted in the owning Chromium
scenario `C_native_workbench`, requires the chronology qualification on the report,
selected-game comparison, and individual finding. An immutable current-source
mismatch does not establish historical repertoire availability or prior study.
This display notice adds no historical authority or inferred timestamps. Exact
completion/introduction/capture timestamps are not supplied by the current finding
contract; the private chronology audit and acceptance limitation remain required.

`C3_mobile_game_board_stays_inside_viewport` in the owning Chromium scenario
reproduced the selected-game view overflowing at390px immediately after desktop
resize. Saved HTML retained Chessground's480px inner plane while its responsive
wrapper shrank. Board painting is now bounded and coordinates stay inside the
board; the original immediate document-width assertion remains unchanged. An
additional assertion requires all32pieces inside the resized board, so clipping
cannot pass by leaving an incomplete board. No timeout budget was increased.

`C3_report_completion_separate_from_input_scope` uses the actual native fixture
with complete input scope and incomplete analysis. It requires both “Analysis
incomplete” and “complete inputs” in the report state, preserving the engine
remaining-work count rather than labeling the report complete from input scope.

`C3_gap_names_opponent_reply_separately_from_learner_response` requires the actual
native fixture's a6 opponent reply directly in finding detail. The learner's
played response remains separate. Display notation comes only from the supplied
opponent FEN/move; it grants no new route authority or move-quality judgment.


Connected navigation: `C_connected_fresh_navigation` verifies an unauthenticated
main-document HTML response, no redirects/API calls, an empty credential form and
API-address guidance. `C_connected_general_study` verifies a root-only, separate
Study tab, no auth/referrer forwarding, no card claim for a gap and no API writes.
`C_connected_session_scope` verifies same-tab reload recovery and a fresh tab's
empty connection without localStorage or cross-window credential transfer.

The regular public-client cases cover root-target validation and category-specific
unverified authority wording. `tests/public-build-config.mjs` executes the owning
Vite config against unsafe public settings before asset emission, without echoing
rejected values. The qualifier includes this config input and checks source
removal; no live/rehearsal Study authority is used by the browser fixture.
