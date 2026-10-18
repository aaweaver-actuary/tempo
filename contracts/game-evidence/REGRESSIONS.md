# game-evidence contract regressions (#264)

## Explicit legacy authority v3

`tests/test_v3.py` owns independent fixture/schema parity, mixed-authority
rejection, exact observation/physical-revision linkage, bounds, known timestamps,
explicit withdrawals and stable authority identity. Existing v1/v2 exports remain
unchanged. Source model cases pass at this checkpoint; JSON Schema, installed
artifact and complete exchange qualification remain pending dependencies/window.

`legacy_scope` structural/digest behavior is additionally exercised through the
Game Evidence scoped-snapshot suite and backend scoped-capture suite. These cover
EOF/highwater shape, stable authority across different games, bounded context,
completed current scan facts and owner acceptance fences. They do not replace
installed contract/schema qualification. Existing exported wire schemas remain.

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

## PR #271 timestamp correction

`tests/test_timestamps.py::test_installed_timestamp_validity_and_original_wire_spelling`
runs this package's own installed public envelope across 37 explicit validity cases,
including fractions, offsets, Gregorian leap rules, year bounds and malformed input.
Accepted strings retain their original offset and fractional representation.

## V2 correction and chronology (#41 / #230–232)

`tests/test_v2.py` owns independently authored v2 round trips/schema parity,
withdrawal-only deliveries, required nullable occurrence time, explicit
encounter/miss/publication/revision linkage, list bounds, and rejection of
implicit withdrawals, inconsistent references and cross-version negotiation.
Existing v1 fixtures, schema and producer output remain unchanged. The installed
Learning remediation suite owns semantic validation against authoritative content;
contract fixtures alone do not establish production adoption or supported misses.

## PR #281 acknowledgement repair

`test_v2_acknowledgement_rejects_duplicate_obligation_ids` rejects independently
authored duplicate IDs through both Pydantic and the committed Draft 2020-12
schema. `test_v2_acknowledgement_preserves_valid_ordered_or_empty_ids` preserves
list order, empty lists and serialization. The parameterized
`test_v2_acknowledgement_retains_status_bounds_and_strict_validation` guards the
existing status/revision/reason rules, batch limit and strict field validation.
