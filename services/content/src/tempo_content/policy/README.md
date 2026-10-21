# Content source policy

**Current unmerged source; deployment unverified.** These are existing decisions moved from the Content database adapter. The [public functions](__init__.py) consume supplied source/row facts; their [implementation](source.py) imports no database, driver, transport or sibling-domain implementation.

```mermaid
flowchart LR
  callers[Content CLI and bridge application] --> store[ContentService adapter]
  store -->|supplied source and row facts| policy[Pure Content source policy]
  store -->|unchanged SQL, locks and sections| database[Content-owned PostgreSQL]
```

| Public function | Existing adapter call site | Decision retained |
| --- | --- | --- |
| `prepare_source` | `ContentService.load_source`, before its first read section | Expected-revision input type/range, workspace binding, canonical payload and stable source/publication/stage identities. The original pure digest function is supplied unchanged. |
| `exercise_continuation` | `_exercise_slice`, between `after_read` and `before_commit` hooks | Existing bounded validation step and declared learner-turn rule. |
| `binding_responses` | `_binding_slice`, between the same hooks | Complete route/occurrence, exact learner decision position and accepted/authored response policy. |
| `publication_report` | `publication_status`, after its existing reads | Validation/error response and the existing 128-definition compatibility report. |
| `coverage_reply` | `coverage`, after its existing direct/transposition evidence reads | Direct coverage takes precedence over transposition support in the returned record. |
| `inventory_report` | `_inventory_status`, after the unchanged source/task/manifest and complete-generation count guards | Retirement-required label for a complete empty source, original source/task field precedence and stale/status fields. |

The [adapter](../../../../../database/tempo_content_database/adapters/store.py) calls these narrow own-domain functions at their original execution points. Queries, parameters, locks, hooks, transaction/snapshot boundaries, fencing, staging, replay and cursor advancement remain there unchanged. No table, stored field, JSON shape, schema or migration changes. The [database dictionary](../../../../../database/tempo_content_database/schema/README.md) remains authoritative for storage relationships.

`tests/test_source_policy.py` exercises the moved decisions with synthetic facts. Existing storage-boundary and installed import/help/resource checks retain ownership of composition and packaging. Native PostgreSQL and the five unchanged loopback transport cases were not run for this slice. The public Content package still eagerly exports its concrete `ContentService`, so this is not a claim that importing the entire package is driver-free.

Worker dispatch and the complete route/exercise/binding phases now live in the [Content worker application](../worker/README.md); publication construction/provenance lives in [publication preparation](../publication/README.md). Further extraction remains explicit work: source staging and publication read/reservation/write sequencing still share `store.py` with persistence operations. These bounded splits do not certify that whole file as application-free or establish a permanent exception.
