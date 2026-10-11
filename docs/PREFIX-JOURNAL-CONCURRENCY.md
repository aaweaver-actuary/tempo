# Prefix-transition journal concurrency and recovery

The prefix-transition browser journal uses the existing exclusive Web Lock
`tempo-pending-prefix-transition-v1:reservation` for all reservation, update,
clearing, and final structural-admission sections. It coordinates cooperating
same-origin tabs in the same browser storage partition. Each writer rereads
persisted ownership while holding the lock; absent or replaced owners cannot
write. Matching verified publication is absorbing and retains its exact result.
The helper retains the lock through the storage task boundary after its synchronous
callback so Firefox commits its task-scoped localStorage snapshot before the next
writer enters. This coordinates the [Firefox task-scoped storage snapshot](https://bugzilla.mozilla.org/show_bug.cgi?id=1868833#c6).
It does not wait for network work or a polling interval.
Intermediate pending/blocked/retrying states have no arbitrary rank. Mutable
patches are applied to the latest record inside the lock; message-only timeout
feedback cannot copy an older receipt state over a newer one.

New confirmations retain optional V1 `approval` evidence: the original reviewed
planner response plus its source target/snapshot/generation and selected routes.
Reservation validates the frozen apply request and exact summary against that
evidence before allocating an ID. A stored request/scope mismatch is unreadable,
including a valid-shape summary that omits shared ownership. Historical V1 records
without evidence remain recoverable under their original ID, but their unresolved
scope is incomplete: independent-looking structural work and new independent
reservations remain paused. `hasCompletePrefixTransitionScopeEvidence` exposes
this distinction. Do not populate missing approval evidence from a fresh plan.

The shared interface is
`withPrefixTransitionJournalLock<T>(signal, criticalSection): Promise<T>`.
The callback performs bounded synchronous ownership/admission work; it must not
await networking or recursively acquire this lock. Synchronous reads and
subscriptions remain available, and `prefixTransitionRecoveryIssue` distinguishes
missing journals, unavailable persistence and unavailable serialization. Recovery
enumeration includes known session identities and always includes the target
repertoire in effective scope.

Network status reads, retries, apply requests, and polling waits are outside
journal critical sections. Structural admission invokes the synchronous dispatch
callback while holding the lock but wraps its response promise, so the response
wait occurs after release. Callers must resolve affected membership beforehand
and perform every mutation delivery through this guard. This is browser
coordination, not a durable cross-device reservation or a substitute for backend
snapshot checks, receipts, and fences.

## Browser compatibility

Prefix journal mutations require native Web Locks in a current browser and a
secure context: HTTPS or a browser-trusted localhost origin. The capability is
checked at runtime. Browsers/contexts without usable Web Locks cannot reserve,
clear, or dispatch a new transition. The error identifies the required browser
and origin. There is no read/check/write localStorage fallback or IndexedDB
coordination subsystem. Known identities remain available for read-only status
inspection. Retry stays disabled while the journal or serialization is unavailable;
Check status stays usable. Study and inspection remain available independently of mutation
admission. See the [Web Locks API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API).

## Recovery and loss of browser data

GET status precedes retry. Apply/retry acknowledgments never prove publication;
matching complete receipt and graph/queue publication evidence do. A rejected
retry followed by still-blocked authoritative status explains that the service
must be restored and another explicit retry admitted. A lost acknowledgment is
ambiguous. Status checking stops after 30 seconds with a message that backend
execution may continue; it retains the latest verified state and original ID,
request, and affected scope. Closing a dialog cancels that caller, not backend
execution.

A mounted JavaScript session retains known identities across component remounts,
failed storage writes, and journal disappearance. Unexpected disappearance keeps
recovery reachable and guards unresolved scope. Verified publication/result also
stays known after deletion until an explicit locked clear; missing storage alone
cannot prove that another tab intentionally cleared it. Status-only recovery never
recreates a missing record or sends an apply request from it. The existing
separate **Restore saved recovery record** action is explicit: it first inspects
status, then under the lock can restore only the exact retained unresolved
identity/request/scope into an absent slot. It refuses any existing record and
never reconstructs approval from a fresh plan. It is separate from Check status;
authoritatively resolved publication needs no restoration. Storage errors are
visible; verified publication received before a failed write stays known in
memory. Keep the page open and retain the displayed operation ID if browser
persistence fails.

A complete page reload destroys session memory. Existing `/api/system/tasks`
can expose application task deduplication IDs, but cannot supply complete
repertoire mapping or the frozen request and omits older completed tasks. A
structural-fence refusal may identify its owning operation. An operator with an
existing ID can inspect `/api/operations/{operation_id}` and retry a durable
blocked operation. Durable receipt/application records can be queried by
repertoire or plan by an operator. Task absence and absence of a browser notice
are not evidence that no command was delivered. The migration snapshot does not
export these receipts/applications. If the sole browser ID vanished before a
receipt was admitted, current interfaces cannot guarantee rediscovery.

## Proposed backend discovery interface (not implemented here)

`GET /api/repertoires/{repertoire_id}/prefix-transition/operations` should query
durable prefix receipts and application rows, including blocked/recovering and
terminal operations, and expose each saved operation ID, original target and
immutable apply payload, trustworthy affected repertoire scope, and the existing
status/result/transition evidence. Return `operations: []` only after a successful
authoritative read; unavailable/inconsistent data must be an explicit error.
Include operations affecting this repertoire through shared ownership, rather
than only operations whose target is this repertoire. The payload must come from
the saved command envelope, not a fresh planner request. Do not fabricate the
original human-reviewed consequences when they were never persisted server-side.

This interface permits status/retry recovery for already admitted operations; it
cannot find an erased browser-only reservation or guarantee absence of broker
messages preceding receipt creation. Stronger recovery requires a separately
designed durable admission/identity contract. Coordinate that work with the
structural/backend owner, without implementing a discovery subsystem in this PR.
