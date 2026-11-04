# Consistency and recovery state machine

The transfer ID remains stable throughout. Each logical operation has a stable
operation ID plus immutable input digest and original receipt. A retry never
becomes a fresh logical operation. Database owners implement bounded operations;
W01 supplies no executor, locks, SQL or live transition procedure.

| Phase | Allowed effect and interrupted disposition |
| --- | --- |
| PREPARE / capture_snapshot | Passive consistent capture with schema, authority, source-generation, PK and original-byte inventories. Source remains sole writer. UNKNOWN required state becomes BLOCK. |
| Optional warm STAGE / stage_batch | Inactive target only. Atomic copied batch + original receipt + checkpoint. Resume same ID/digest; changed digest conflicts. No serving, grading, admission or source writes. |
| Preliminary VERIFY / verify_staging | Compare warm manifest; never authorizes activation. Incomplete batch resumes or remains held. |
| FENCE SOURCE WRITES | Owning enforceable boundary must deny existing transactions, stale credentials, worker/CLI restarts, alternate SQLite authority and privileged bypasses. Retain original maintenance lock. F13 native negative proof is mandatory; process stop is insufficient. |
| Final capture_snapshot | Capture consistent final watermark after successful denial. Bind fence ID. Browser late arrivals remain original-key reconciliation/quarantine, not silently omitted work. |
| Final delta/rebuild + FINAL VERIFY | Reconcile every post-warm change, including accepted operations, receipts and source generations. New manifest invalidates old verification. All required stores/families and target generation match. |
| ACTIVATE | One Learning-owned atomic generation/authority commit, under retained fence/lock. Exact final receipt and proposed generation must match. No cross-database atomicity claim. Persist original activation receipt. |
| FINALIZE / verify_runtime | Actual acceptance readback, exact artifacts/schema/modes, one writer and saved-review→receipt→playable successor. Source remains fenced. No automatic deletion or fallback. |

Before target acceptance, a return to the original writer requires proven no
target authoritative effect, retained locks, verified source state, and proven
target denial. Absence of a response is not proof of no commit. After any new or
uncertain target commit, forward repair is mandatory unless separately proven
lossless reversal exists. The actual authority owner's committed readback
decides acceptance status; an operator-supplied `none` is not sufficient evidence.
No phase clears browser journals, cancels accepted debt, synthesizes human
grades, restores stale state or deletes original history.

All six logical operations and their phase IDs must be implemented using
existing owner adapters before F14. Source fencing and activation are separate
commits joined by enforced authority and final verification, not a distributed
transaction. Timeout/cancel leaves durable original identity and a truthful
pending/held/conflict disposition. Actual retry limits follow measured owner
budgets; W01 invents no global deadline.
