# Workbench owning regressions

`tests/client.test.mjs` via `npm test` owns authenticated origin/workspace delivery,
foreign-response rejection, stale connection/JSON-decoding fencing, capability and policy validation, and explicit auth failure.
`tests/browser.mjs` owns `C_native_workbench`: strict declared traffic, native
connection/filter/detail/board controls, pending dismissal receipts, a lost POST response and authoritative receipt/state recovery after real reload, and phone bounds.
The explicit producer JSON must come from the native Intelligence runner. Missing
fixture/browser/build and unexpected traffic fail. No backend or live API fallback.
`qualify.mjs` owns source-removed installed public-client and native browser proof.
