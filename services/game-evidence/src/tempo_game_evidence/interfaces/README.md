# Game Evidence interface

Owner: #491, `game-ingestion` in allocation revision 2.

The existing `ComparisonSnapshot`, `ComparisonSnapshotV2`, legacy/scoped snapshots
and `PhysicalCard` retain their validation and identity. `parse_snapshot` validates
supplied snapshot facts; `preview_legacy_observation` prepares an immutable
observation from explicit retained source. Neither operation admits learning,
publishes content, opens a database, sends delivery or starts a worker.

Dependencies are pure chess/repertoire comparison, canonical ingestion and
existing wire contracts. Their values may appear in snapshot fields; this surface
does not re-export their implementations. In particular `EvidenceStore` and the
foreign ingestion alias `normalize_pgn` are intentionally absent. Storage belongs
to its adapter owner; byte normalization belongs to `tempo_game_ingestion.interfaces`.
The owned `source.py` caller now imports that public ingestion surface directly;
the normalizer identity and import/write preparation order remain unchanged.

The old package initializer still eagerly imports its legacy database store class
without opening a connection. This exact pre-existing edge is not decoupling or
an export sanctioned by this interface. #501's package/caller migration and
#482's guard must remove that legacy barrel after consumers move. This checkpoint
changes only the allocated surface; no runtime adoption is claimed. Cold import
has connection/provider/process/thread tripwires and export identity checks in
`backend/tests/test_public_surface_game_ingestion.py`.
