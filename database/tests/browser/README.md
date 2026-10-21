# Browser database proof fixtures

[`study-learning-storage.mjs`](study-learning-storage.mjs) contains nine existing native IndexedDB callbacks from [`prove-study-learning.mjs`](../../../scripts/prove-study-learning.mjs). The qualifier still selects scenarios, owns Playwright lifecycle/UI/HTTP/assertions and invokes the same callback at the same point with the same arguments. Each exported callback keeps its original body, so Playwright serialization remains closure-free.

The coupled bootstrap body-fault callback is retained intact as a native test fixture: its IndexedDB transaction-completion observation and HTTP response-body interruption hooks run in the same injected callback and order. This is proof instrumentation, not runtime application/domain policy.

The callbacks read the existing `tempo-study-intents` database's `intents` store; seed the existing legacy/incompatible store versions for fault scenarios; simulate IndexedDB unavailability, quota and abort errors; and read the selected Learning authority's prepared queue and first offline attempt. The existing `tempo-learning-authorities-v1` WebStorage registry is read only to select that native fixture database, with the same parsing and selection as before. These are test fixtures, not another runtime storage implementation.

| Existing fixture storage | Key and value facts used here |
| --- | --- |
| `tempo-study-intents` / `intents` | Composite key `[workspace,key]`; legacy seed stores `workspace`, `key`, `state`, `endpoint` and `payload.outcome`. Version 1 and version 2 fixture definitions are unchanged. |
| Authority-specific offline database / `training` | Fixed key `prepared-daily-queue`; the proof reads `cards`, `attempts` and the first attempt without changing their stored shape. |
| `tempo-learning-authorities-v1` | Existing JSON authority registry supplies `offlineDatabase`; the fixture still selects the first existing entry. |

No schema/model change or live/browser storage inspection was performed. Existing pure source/fake evidence can establish the move and callback identity; native browser qualification remains separate and was not run for this relocation.
