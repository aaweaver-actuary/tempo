# History authority source binding

`history_authority.py` is pure source preparation for #358/#389/#267. It reuses
the existing #388 normalized snapshot contract; it creates no database model,
exporter, transaction/writer, replay executor, or accepted-transfer receipt.

## Public contract

`canonical_packet_sha256(packet)` hashes finite JSON using UTF-8,
`sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=False`, and
`allow_nan=False`. Source readers must reject duplicate JSON members. Array order,
unknown provenance, original row keys and payload values remain part of the hash.

`approved_source_validator(checkpoint, leaf, independent_approval=capability)`
returns the `approval(source)` callback injected into one converter. Leaf names
are `content_identity`, `reviews_schedules`, `server_receipts`, `browser_intents`.
The callback requires the complete source packet digest approved for that leaf,
integer `schema_version: 1`, empty `unresolved`, and this exact authority reference:

```json
{"fingerprint_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

This is a synthetic example, not an installed identity. The fingerprint names
the exact private authority evidence artifact bound by the #389 checkpoint. That
review must establish installed bytes/migrations, actual source and destination
identity/workspace binding, original browser authorities, pending-state coverage,
and the authorized snapshot. A digest neither supplies missing facts nor proves
the artifact is trustworthy. The leaf never interprets or relabels its authority.

The checkpoint is the public result of the separately owned
`study_mapping_checkpoint.validate_checkpoint`. Required result fields are
`consistent: true`, `evidence_kind: 'actual'`, `actual_release: true`,
`authenticated: false`, `final_acceptance: 'unverified'`, `approved_leafs`,
`authority_fingerprint_sha256`, `checkpoint_sha256`, and `leaf_source_sha256`.
`checkpoint_sha256` binds the complete validated checkpoint, including exact
snapshot/map/golden/authorization bindings and named reviewers. The independent
capability must verify that exact checkpoint identity. `actual_release` means
supplied-evidence eligibility only. The composition root must inject an independent
capability verifying that exact checkpoint; it must return boolean `True`.
Self-labelled JSON cannot replace that capability. No live trust adapter or
actual approved evidence is supplied here. Synthetic tests use explicit fakes.

Unknown mappings/dispositions fail closed in each leaf even if its bytes match.
Packet binding cannot approve an unspecified authority transfer, regenerate an
answer, regrade history, or activate an old lease. It does not authorize target
writes, replay, launcher activation or cutover. Final real-data rehearsal,
interruption/repeat/conflict/replay evidence, verified backups and separately
authorized cutover remain mandatory.

## Ownership and direction

```mermaid
flowchart LR
    evidence[Private exact-version evidence and independent approval]
    checkpoint[Public checkpoint result]
    root[Outer composition root]
    binding[Pure source binding capability]
    leaf[One injected converter leaf]
    normalized[Existing normalized group records]
    evidence --> checkpoint
    checkpoint --> root
    root --> binding
    binding -->|approval callback| leaf
    root -->|frozen source packet| leaf
    leaf --> normalized
```

Source: [`history_authority.py`](../history_authority.py). Each leaf receives a
capability; it imports no sibling converter and discovers no service. A missing or
failed leaf blocks its history qualification only. Ordinary Study does not import
or wait for this tooling. The root owns wiring and cannot overwrite duplicate
group keys. Reviewers/authority evidence, projection and target transactions have
separate owners. This drawing describes implemented source contracts; actual
installed-history qualification and deployment are unverified.

Focused check: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s database/tests -p test_history_authority.py -v`.
