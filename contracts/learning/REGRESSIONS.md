# learning contract regressions (#264)

Owning suite: `tests/test_wire.py` plus the subsystem reference test file.
Exact collected identities are reported by pytest; empty selection fails.

- `test_independently_authored_producer_round_trip`
- `test_exported_json_schema_matches_authoritative_model`
- `test_future_versions_and_unknown_fields_are_rejected`
- `test_every_public_interface_has_a_producer_example`
- `test_imports_resolve_to_installed_artifact`
- `test_non_json_numbers_and_coercions_are_rejected`

Behavior-specific tests document replay, authority, fencing and failure semantics.
The shared installed-artifact and dependency proof lives in `../tests`;
TypeScript consumers live in `../consumer-examples`.
