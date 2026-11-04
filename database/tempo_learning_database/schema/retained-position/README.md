# Representative retained position staging

`0004_retained_position.sql` defines non-serving staging tables. `retained_position_states` retains the exact logical position, source/epoch, version, FSRS bytes and internal ID, due/introduction timestamps, and donor identity. `retained_position_facts` stores original captured bytes, full source and target keys, digest and source references, including review invalidation, plans, receipts and grades. `retained_position_batches` owns immutable transaction receipts. The caller owns transaction and namespace inventory completeness; the adapter does not activate the target, invoke a scheduler or certify migration parity. Stored facts are not runtime review tables. Reconciliation, complete source capture, namespace fencing and behavior after transfer require separate owners and proof.

| Source facts | Target representation | Disposition and identity |
| --- | --- | --- |
| `study_position_states` full row, including FSRS TEXT, due, introduction, definition, epoch, version, donor | `retained_position_states` and `retained_position_facts` | EXACT; repertoire, variant, position; duplicate key conflicts |
| Epoch, owner, seed run/outcome, issued plan, first response, grade | `retained_position_facts` | EXACT original full primary key, bytes, digest and source references |
| Donor card, repertoire, queue/day, reviews, invalidations, receipts and review schedule snapshots | `retained_position_facts` | EXACT original full primary key, bytes, digest and source references |
| Original transfer and batch | `retained_position_batches` | Same digest and receipt replay unchanged; changed binding conflicts |

The selected synthetic source uses PostgreSQL `row_to_json` UTF-8 as a fixture codec. That captures the database's typed JSONB representation, not original JSONB input formatting; TEXT values remain unchanged. A real exporter, complete inventory, checked namespace fence and an executable target mapping remain unverified. No staged fact itself grants issuance, review or activation authority.
