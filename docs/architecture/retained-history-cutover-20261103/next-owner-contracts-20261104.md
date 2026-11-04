# Owner contract review and pure implementation handoff

The [proposed contract packet](https://github.com/aaweaver-actuary/tempo/issues/908#issuecomment-6628451806) was reviewed independently by three Sol specialists, followed by CM cross-owner review. Existing swarm identities, PRs and receipts were preserved. These decisions approve the following **pure subsets**, not complete producer authority or cutover.

| Owner | Implemented boundary | Limit |
| --- | --- | --- |
| Content | `tempo_content.generation_envelope.validate_content_generation_envelope` checks a declared learning-profile certificate/export, raw and canonical digests, exact declared inventories and namespace bindings. | Every result remains `UNVERIFIED`; consistent forged/omitted source declarations cannot establish source completeness, approval, a real fence or durable replay. |
| Learning | `tempo_learning_runtime.original_plan_mapping.map_original_plan_to_slots` validates the original full canonical plan and maps every ordered decision occurrence, preserving repeated/transposed positions and due witnesses. | Detached projection dataclasses confer no authority. No grade, issuance, native finality or serving activation occurs. |
| Tactics | `app.services.tactics_asset_inventory.inventory_tactics_asset_bytes` inventories supplied raw catalog/pack bytes and ordered memberships. | Historical authority remains `UNPROVEN`; it does not inspect runtime roots, symlinks, caches, archives, DB watermarks or browser journals. |

The Content `learning` profile preserves authored routes, bindings and aliases while omitting optional legal-reply inventory; the existing export was reused. Legacy JSONB preserves semantic values and the accepted canonical digest, not unavailable original transport bytes. Its ASCII canonical codec differs from Content's UTF-8 canonical codec. Legacy original-source digest, row authored digest and Content source digest are distinct domains. A future verified projection must bind those domains and complete repertoire/route/policy/ply namespaces explicitly; equal route text or revision numerals are insufficient.

Original source reference was #929 `f811bcde7f060585d924cd04ed397c13b172cf84`, now included by ancestry in main through #935. This integration preserves #930's qualifier invocation fix and the exact leaf source from #931/#932/#933/#936, adding declared module exports, public interface descriptions and regression ownership. Those pure registrations changed no transaction or serving authority; subsequent producer milestones are recorded below.

## Focused evidence

Executable integration candidate `227a77f049fb3e7fa6e8b4d5c487101ed5462d42` passed Content/Learning Python source boundary checks with their declared build inputs. On task-local Python 3.12 packages, the changed Content public facade passed three owner-reconciled tests; guarded runtime import opened no connections, pools, threads or subprocesses; the regular runtime mapper selection passed seven tests. The regular backend Tactics selection passed 19 tests using the existing Python 3.14 interpreter explicitly. An export-order mismatch was repaired before these passes; its original failure receipt remains retained. These import checks do not establish a full artifact matrix or installed Tempo behavior.

Unchanged leaf semantic evidence is reused: #933 Content selected 123 pure tests; #932 mapper 7 pure cases and two pinned original-codec fixture roundtrips; #931 supplied-byte inventory 19 focused cases. No full native suite was repeated for registration or prose changes. Private pure receipts: `/private/tmp/tempo-contract-integration-evidence-20261104/integration-summary.json` and the linked owner collection/execution/JUnit records. L3 regression receipts: `/private/tmp/tempo-l3-followup-evidence-01a1a5b0/run-7e72da402806c675` (reproduced failure) and `run-b09a03a8fb819a75` (pass).

## Remaining gates

C3's supported Content writer reservation, owner-selected immutable capture and recovery implementation passed independent review at `6c2981bde9ebc1059a273df9cfcea07ead6690d7`; source integration and its package qualification are separate from the original pure envelope. Actual target disposition, legacy correspondence and installed authority remain absent. L3 original source readers are implemented in #936 at `4e863d689194c3c0173ca8470717a42efa818496` and integrated without source changes. One focused native case passed on PostgreSQL 18.6 / schema 59 / Python 3.14.8, covering actual original issuance/response/aggregate save, later mistakes, assistance, low-level fixture-origin alternatives, exact identity/precision, repeatable snapshots and explicit conflicts. The required pure decoder is injected from actual source composition in the fixture; no production wiring or clean-prefix weakening was introduced. Independent CM review found missing canonical scheduling and exact source-reference predicates; the unsafe committed result was reproduced before repair, and the expanded native selection passed afterward. The runner recorded prior parent revision `c09eb748`; both tested file SHA256s were independently matched to the final committed source. Owned container, volume and test data absence were verified for the failing and passing runs. Native receipts remain component evidence on their recorded source/dependencies, not an assembled target proof. Migration 057 alone did not establish post-commit original-response mutation denial; additive legacy060 now supplies that bounded LP07 guard (see continuation below). Prototype0007 protects different tables; L4 remains separate.

T3 historical binding cannot recover unrecorded served catalog generations by archiving today's bytes. Original process/cache/root or immutable release evidence and archive readback are required; browser-local authority remains separate. Full cross-owner C-FENCE/L4/T3, assembled saved-review → playable-next, H5 recovery rehearsal and hosted merge qualification remain unproved. No live data/service changes, migration, installation or activation occurred; cutover remains **NO-GO**.

## Reviewed source delivery, November 4 continuation

Main #935 merge `df9f35943af7a15a55cb8b417ee83ad0d5464e3e` includes the
reviewed pure subsets, L3 reader and #937 qualification repair. Runtime installed
qualification passed three cases in each Python 3.12/3.14 wheel/sdist combination;
Content passed 364 in each corresponding source-removed combination plus one
installed interoperability case and probe. Main merge tree matched the tested
Content qualifier head. These are component artifact proofs, not installed Tempo.
Actions was disabled and main had no effective enforced branch rules: merges are
recorded `ci-disabled`, with no hosted success or release-gate waiver inferred.

Main #941 merge `1e7840c9e977332d49d16454cdcfcacdf372c484` delivers #938's
fixture-only prospective asset archive (5 generation/7 archive owner tests) and
#939's six-table native read-only row inventory (one schema-59 PostgreSQL case).
Independent Sol review accepted corrected archive descriptor/ABI handling and
verified native receipt source hashes and cleanup. Their canonical owner views
retain unselected activation, historical `UNPROVEN` and `PRELIMINARY_UNBOUND`.

C3 #940's original terminal-state race was reproduced before repair (95 passed,
one named failure), then its exact corrected owner selection passed 96/96 native
and 98/98 pure cases. Independent review confirmed locked terminal checks and
epoch CAS prevent late capture/hold/publish reviving accepted or aborted epochs;
owned fixtures were removed. This is supported Content-owner authority only.
The integrated public module candidate `d83eb140f714bee38096847f51e13a5cb2246390`
passed source-free Content qualification: 365 cases in each Python 3.12/3.14
wheel/sdist combination, plus one independent installed consumer case and probe.
All 146 Content source fingerprints matched; source/temp consumer cleanup passed.
The initial 360-pass/four-failure fake-composition result remains retained; the
two-test-only repair preserved real guard execution and added reserved denial.
Main #942 merge `19f9bbaae748223d1aaea8207b09cc439236c1db` delivers this source.
No live schema application, installation or activation follows.

Full contract proposals remain proposals beyond these explicit frozen subsets.
The next owner mapping must bind original legacy plan/source digest domains to
Content capture and exact route/policy/ply identities without guessed history.
Real target receipts and atomic Learning
effect/receipt/checkpoint remain separately owned gates. No live operations.

LP07 additive source migration060 rejects original first-response UPDATE/DELETE
(including no-op) and reachable TRUNCATE with23514; PostgreSQL may first refuse
single-table TRUNCATE with its existing FK0A000. Existing inserts, exact replay,
accepted identity/timestamps and grade rollback remain intact. Independent L1
review accepted final owner source000111ca and native two-case proof at
PostgreSQL18.6/schema60; retained precommit revision is bound by matching hashes.
This closes bounded durable original-response immutability, with privileged
DDL/replication bypass and actual installed migration still unqualified.
