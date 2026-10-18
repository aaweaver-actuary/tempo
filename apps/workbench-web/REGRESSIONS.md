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
