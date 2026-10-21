# Legacy Games command storage

**Current source in the unmerged extraction; live deployment unverified.** These modules contain the existing parameter-bound queries, row/advisory locks and row-count/read projections formerly mixed into backend command files. They borrow the command or worker's existing connection and transaction; none opens, commits or rolls back a connection.

| Storage module | Existing caller composition | Stored data / responsibility |
| --- | --- | --- |
| [mutations.py](mutations.py) | [game_commands.py](../../../../backend/app/game_commands.py) | Imported-game exclusion, finding exclusion, derivation generation and existing repertoire-match facts. |
| [synchronization.py](synchronization.py) | [game_sync_commands.py](../../../../backend/app/game_sync_commands.py) | Provider-sync state, admission advisory lock, jobs and deterministic provider windows. |
| [findings.py](findings.py) | [finding_commands.py](../../../../backend/app/finding_commands.py) | Locked current-finding facts, curation/defer/decision writes and canonical linked miss-event lookup. |
| [finding_cards.py](finding_cards.py) | [finding_card_commands.py](../../../../backend/app/finding_card_commands.py) | Existing finding/game/opportunity lock order, saved-card identity facts, legacy finding-card/link writes and queue-position lock binding. |
| [analysis_leases.py](analysis_leases.py) | [game_analysis_commands.py](../../../../backend/app/game_analysis_commands.py) | Parent/position lease reads and guarded writes, reports/errors, retries and existing one-row repair operations. |
| [analysis_publications.py](analysis_publications.py) | [game_analysis_publication.py](../../../../backend/app/game_analysis_publication.py) | Publication admission, bounded staged move/candidate writes, cursor facts, published-generation switch and follow-up lookup/write operations. |

Application validation, HTTP outcomes, provider-window policy, chess/engine computations, UUID/time selection, eligibility/generation/lease decisions and durable task orchestration remain in the caller files outside `database/`. The storage operations return fetched facts or primitive row counts, never cursors. The existing signatures and command registrations remain in the original composition modules. No generic SQL execution port, runtime dispatch layer or new framework is introduced.

```mermaid
flowchart LR
  caller[Legacy command and worker composition] -->|borrow existing transaction; semantic operations| storage[Legacy Games SQL adapters]
  caller --> decisions[Existing validation and application decisions]
  storage --> tables[Existing legacy shared database tables and views]
  caller --> seams[Existing durable task and queue writer seams]
```

This is a physical/query separation within the existing legacy owner. The finding-card path already writes legacy `cards`, `repertoires` and `repertoire_cards` and calls the existing queue writer; finding decisions already call the existing real-game feedback seam. Those existing couplings are preserved and disclosed, not presented as isolated Games ownership or a new permission to access another component's tables. Ordinary Study gains no Games import, call or lifecycle dependency from this move. Strict domain wiring and independent resource deployment are not established by this batch.

The [source-schema charts and complete field/key dictionaries](../../schema/README.md) remain authoritative for actual stored fields, keys and FKs. No table, column, FK, migration, constraint, view or stored JSON format changes here. No live schema or data was inspected. Table/view names in these queries retain their existing owner; logical queue/card/game identities are not extra FK constraints.

Focused tests retain the existing game exclusion/sync admission, finding/card, manual publication, generation/lease fencing and rollback assertions, using fake connections or task-owned in-memory SQLite only. Native PostgreSQL proofs remain separate and unexecuted for this relocation. Source comparison confirms all distinct SQL literals match the pre-extraction source exactly; this is one-time relocation evidence, not a new SQL-literal test framework.


[Derivation storage](derivation/README.md) owns the existing position/event stages, job projections, feature publication and miss/priority queries. Application workers keep chess/phase/task decisions and the same borrowed transactions.


[Provider synchronization](sync/README.md) and [repertoire staging](repertoire/README.md) contain complete original sync/claim/window/comparison queries. Worker normalization, fetch, phase, task/lease and publication decisions remain outside database with their existing transaction authority.


[Legacy provider import storage](imports/README.md) and [event/finding storage](evidence/README.md) hold the existing imported-game/state, gameplay-event and staged finding/publication operations. Provider/chess/classification, task/retry and publication decisions remain at the original application seams.


[Coordinator storage](coordinator/README.md) contains the original recovery/claim/status/settlement queries; the coordinator retains process/async/provider/task lifecycle. Shared claim SQL builders remain available through their old composition names.


[Authority, finite capture and acceptance storage](authority/README.md) owns the existing reader/tuple writer, column metadata, bounded stream SQL and receipt/acceptance operations. Application scope/budget/digest/chess/lineage and command outcome decisions remain outside database.


[Analysis-worker position storage](analysis_worker/README.md) and [legacy finding projections](finding_projections/README.md) own the existing report/lease, threshold/candidate and finding/opportunity/priority operations. Scoring/chess/evidence/recommendation, time/attempt/lease decisions and callback order remain in the original worker/application files. The existing command adapter `findings.py` remains distinct.
