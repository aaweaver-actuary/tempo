# Optional opening-ranking capture reads

**Current source; live schema and deployment unverified.** [storage.py](storage.py) owns the existing complete read-only SQL, bounded raw-row transfer, native cursors and fixed five-group query preparation from [opening_ranking_snapshot.py](../../../../backend/app/services/opening_ranking_snapshot.py). It borrows the existing authoritative repeatable-read connection. It opens no connection, starts no transaction, writes no data and changes no SQL, parameter order, field, constraint or schema.

```mermaid
flowchart LR
  app[Opt-in application capture, selection and validation] --> prepare[Root candidate SQL preparation]
  prepare --> admission[Existing application deadline and background admission]
  admission --> connection[Existing authoritative read-only repeatable-read section]
  connection --> boundary[Root database timestamp and production-day read]
  boundary --> day[Application requested-day guard]
  day --> groups[Root fixed candidate, route, repertoire, introduction and epoch specs]
  groups --> reads[Root bounded native raw-row reads]
  reads --> budget[Application row, byte and deadline guards]
  budget --> close[Existing connection and admission sections close]
  close --> project[Application JSON decoding, planning and snapshot projection]
```

## Operations and retained compatibility

| Root operation | Application responsibility retained |
| --- | --- |
| `prepare_capture_queries` freezes the existing priority-page SQL and selected repertoire parameters; its one fixed binder constructs the original five query specifications. | Validation, selection/policy, timezone, limits, admission and deadline choices. Candidate SQL is still constructed before the first monotonic clock read; five specifications bind only after the database boundary and requested-day guard. The fixed binder preserves these two original preparation points; it is not a dispatcher or query registry. |
| `capture_boundary` performs the original native timestamp/day read. | Deployment timezone selection, requested-day mismatch error and historical-capture restriction. Server `transaction_timestamp()` remains the time source. |
| `bounded_rows` performs the original windowed byte-count query, `limit + 1` transfer bound and `fetchall`. | `_raw_rows` keeps its published signature and monkeypatch point, post-fetch row/byte guards, raw-JSON tuple and byte count. The outer application loop decrements the aggregate budget and checks the same deadline/foreground conditions at the same points. |

The preserved `_raw_rows(database, query, parameters, *, limit, remaining_bytes)` is an existing **legacy query-shaped compatibility surface**, with the same data flow and no SQL implementation outside this root tree. It is not a clean semantic storage port; review its boundary in the later responsibility phase rather than certifying it as one here. No new callable/query dispatcher, generic SQL port, registry, class or independent transaction authority is introduced. The original priority-page and outstanding-miss imports remain compatibility names; their SQL implementations are [root queue projections](../queue_refresh/openings.py) and [miss projection](../real_game_miss_projection.py).

The existing application still closes its connection before JSON decoding, planner execution, snapshot hashing/projection and final byte checking. Exceptions, foreground deferral, ten-second capture deadline, 10,000 physical-candidate and 40,000 raw-row ceilings, 4 MiB aggregate budget and production planner basis remain unchanged. This opt-in capture is not called by product queue reads. Existing legacy queue/priority/graph/Study coupling is preserved, not proof of optional-feature failure isolation or permission to access another owner’s tables. The existing miss projection imports PostgreSQL storage, which imports application background timing; this legacy transitive dependency remains, and discovery requires both the backend and root database paths. This directory is not an independently packaged service.

## Stored facts and canonical ER/dictionaries

Owner: existing legacy backend, PostgreSQL `public`. This read-only operation creates no tables. The linked canonical schema READMEs contain every stored field/type, PK/composite key, enforced FK/cardinality and explicitly logical relationship; similar identifier names do not establish constraints. Current source tables/migrations are authoritative here, not an inspected live database.

| Stored source | Facts read / purpose | Canonical ER and dictionary |
| --- | --- | --- |
| `cards`, `repertoire_cards`, `repertoires` | Executable card/revision/start FEN/moves, state/due/introduction/archive/validation fields; direct and linked repertoire membership, name and quota. | [Card dictionary](../../schema/study-queue-review-1/README.md#publiccards), [authored source index](../../schema/README.md), [repertoire dictionary](../../schema/authored-content-3/README.md#publicrepertoires) |
| `opening_graph_steps`, `opening_graph_publications` | Current published route generation, line/decision ordering and complete step facts; graph state/publication timestamp. | [Opening graph ER and complete fields](../../schema/opening-graph/README.md) |
| Current gameplay, opportunity and repertoire-priority projections; priority preparations/publications | Existing active-miss/priority reason and date, score/completion/frontier/evidence JSON, generation/scoring version/update/publication and prepared source identity. Existing root page SQL retains its eligibility predicates and integrity-block exclusions. | [Priority ER and dictionaries](../../schema/discovery-priority/README.md), [all related source dictionaries](../../schema/README.md) |
| `daily_queue`, `settings` | Current-day first-cycle distinct introduced-card counts by admission repertoire; fallback new-card quota. Queue exclusion and planner reproduction remain existing behavior. | [Queue dictionary](../../schema/study-queue-review-1/README.md#publicdaily_queue), [settings dictionary](../../schema/runtime-metadata/README.md#publicsettings) |
| `priority_repertoire_source_epochs`, `priority_source_epoch` | Existing repertoire/global version facts recorded in the exported snapshot source metadata. | [Epoch fields and keys](../../schema/authored-content-1/README.md#publicpriority_repertoire_source_epochs) |

The canonical ER charts stay in their schema READMEs rather than being duplicated. Focused fake/source checks cover SQL/parameters, preparation timing, database-day and deadline guards, byte limits, monkeypatch behavior and projection after connection closure. Native PostgreSQL/admission behavior, deployed resource isolation, live schema and full-system qualification remain unverified; no application startup, provider or native run is claimed.
