# Supported producer/consumer combinations

The first reusable artifact revision is **1.0.0**, wire **v1**. Every envelope requires integer version `1`; boolean/string versions, future version `2`, missing required fields and unknown fields are rejected. There is no negotiation fallback to an older shape or sample result.

| Producer | Consumer | Exact coverage | Status |
|---|---|---|---|
| Python package 1.0.0 / wire v1 | Independent Python 1.0.0 examples | All five packages, all 28 top-level interfaces and 78 hand-authored positive records | Tested |
| Python package 1.0.0 / wire v1 | TypeScript consumer 1.0.0, AJV 8.20.0 + independent semantic checks | The same 78 exchanged producer records, independently maintained expected outcomes; 22 semantic negatives | Tested on Node 26.10.0 |
| Python 1.0.0 producer | Synthetic Python 1.0.1 consumer / unchanged wire v1 | Metadata-only patch wheels built and installed in restricted environments; every positive record consumed | Tested on CPython 3.12 and 3.14 |
| Synthetic Python 1.0.1 producer | Baseline v1 consumer expectations/schema artifacts | Producer output compared structurally after JSON parsing to 1.0.0 records; unchanged installed schema exports verified | Tested; synthetic patch is not a released artifact |
| Content 1.0.0 publication | Learning 1.0.0 executable definition consumer | All five exercise variants through independently defined public types | Tested |
| Game Evidence 1.0.0 obligation | Learning 1.0.0 obligation consumer | Stable identity, source lineage and publication generation preserved | Tested |
| Legacy current application APIs | New v1 contracts | Source mapping only; no production adapter or runtime exchange | Unverified / unsupported as a compatibility claim |
| Wire v2; arbitrary N/N−1; Python 3.13; other Node runtimes; published registry packages | Any consumer | No tested matrix supporting those claims | Unsupported qualification claim |

Python metadata permits 3.13 within the supported syntax/runtime range, but this PR does not claim a 3.13 execution result. Node metadata retains >=22.13.0; only the observed runtime above is qualified. Packages are installable local wheel/sdist artifacts, not uploaded to PyPI/npm.

Patch versions preserve v1 behavior and schema exports. A change to accepted fields, states, required information, identity semantics or observable effects is not automatically compatible merely because it appears additive. Unknown fields are rejected. Breaking changes require a new version-addressable namespace and explicit producer/consumer tests before supported combinations expand. No arbitrary rolling-upgrade window is promised.

Fixture paths are `contracts/<root>/fixtures/v1/*.json`; each file identifies its public model and explicit payload. Examples are hand-authored, not derived from JSON Schema generators. Python consumers and the TypeScript expectation table are separately maintained. `semantic-negative/v1` records cover coherence beyond structural schemas. `schemas/v1/*.schema.json` exports are included in installed artifacts; schema changes cannot silently drift from Pydantic definitions.

`qualify_artifacts.py` copies only declared package inputs to a temporary directory, builds wheels and source distributions, installs wheels into a clean environment, runs suites through isolated Python, and creates metadata-only patch wheels there. Unrelated application, backend, model and provider source directories are absent. All temporary source copies/environments are removed afterward. This is bounded artifact proof, not the other agent's general build/discovery infrastructure.


## Correction to the unaccepted 1.0.0 foundation

PR #271's repair keeps artifact 1.0.0 / wire v1 and existing model/envelope names.
It intentionally stops accepting malformed timestamp components that earlier
validation could normalize (for example +00:60 and hour 24). This is a correction
to the unaccepted foundation, not evidence that arbitrary acceptance-changing
patch releases are compatible. The synthetic 1.0.1 qualification remains a
metadata-only, unchanged-behavior exercise. The table above records original
foundation coverage; new repair results are separately recorded in VALIDATION.md.

The reference ordering example now requires caller-supplied expected_policy_id
and expected_policy_revision; neither has a default and neither comes from incoming
advice. These are reference-consumer arguments, not new wire fields. Advisory
semantics apply identically to standalone and nested forms. Timestamp comparisons
retain all microseconds and offset equivalence without rewriting serialized strings.
