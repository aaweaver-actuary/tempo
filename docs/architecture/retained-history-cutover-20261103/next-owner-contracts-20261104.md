# Owner contract review and pure implementation handoff

The [proposed contract packet](https://github.com/aaweaver-actuary/tempo/issues/908#issuecomment-6628451806) was reviewed independently by three Sol specialists, followed by CM cross-owner review. Existing swarm identities, PRs and receipts were preserved. These decisions approve the following **pure subsets**, not complete producer authority or cutover.

| Owner | Implemented boundary | Limit |
| --- | --- | --- |
| Content | `tempo_content.generation_envelope.validate_content_generation_envelope` checks a declared learning-profile certificate/export, raw and canonical digests, exact declared inventories and namespace bindings. | Every result remains `UNVERIFIED`; consistent forged/omitted source declarations cannot establish source completeness, approval, a real fence or durable replay. |
| Learning | `tempo_learning_runtime.original_plan_mapping.map_original_plan_to_slots` validates the original full canonical plan and maps every ordered decision occurrence, preserving repeated/transposed positions and due witnesses. | Detached projection dataclasses confer no authority. No grade, issuance, native finality or serving activation occurs. |
| Tactics | `app.services.tactics_asset_inventory.inventory_tactics_asset_bytes` inventories supplied raw catalog/pack bytes and ordered memberships. | Historical authority remains `UNPROVEN`; it does not inspect runtime roots, symlinks, caches, archives, DB watermarks or browser journals. |

The Content `learning` profile preserves authored routes, bindings and aliases while omitting optional legal-reply inventory; the existing export was reused. Legacy JSONB preserves semantic values and the accepted canonical digest, not unavailable original transport bytes. Its ASCII canonical codec differs from Content's UTF-8 canonical codec. Legacy original-source digest, row authored digest and Content source digest are distinct domains. A future verified projection must bind those domains and complete repertoire/route/policy/ply namespaces explicitly; equal route text or revision numerals are insufficient.

Source reference is unmerged #929 `f811bcde7f060585d924cd04ed397c13b172cf84`. This integration preserves #930's qualifier invocation fix and the exact leaf source from #931/#932/#933, adding declared module exports, public interface descriptions and regression ownership. No state owner, SQL transaction, schema, serving composition or lifecycle changes; no new architecture drawing is required.

## Focused evidence

Executable integration candidate `227a77f049fb3e7fa6e8b4d5c487101ed5462d42` passed Content/Learning Python source boundary checks with their declared build inputs. On task-local Python3.12 packages, the changed Content public facade passed three owner-reconciled tests; guarded runtime import opened no connections, pools, threads or subprocesses; the regular runtime mapper selection passed seven tests. The regular backend Tactics selection passed19 tests using the existing Python3.14 interpreter explicitly. An export-order mismatch was repaired before these passes; its original failure receipt remains retained. These import checks do not establish a full artifact matrix or installed Tempo behavior.

Unchanged leaf semantic evidence is reused: #933 Content selected123 pure tests; #932 mapper7 pure cases and two pinned original-codec fixture roundtrips; #931 supplied-byte inventory19 focused cases. No full native suite was repeated for registration or prose changes. Private receipts: `/private/tmp/tempo-contract-integration-evidence-20261104/integration-summary.json` and the linked owner collection/execution/JUnit records.

## Remaining gates

C3 still needs Content's durable source reservation, complete writer/lease denial, original source-selected inventory and recoverable final capture. L3 original source readers are a separate bounded native task; no completed reader proof is asserted here. A required pure decoder capability must be injected by source composition, without a DB-to-runtime dependency or weakening the clean-prefix reader. Migration057 does not establish post-commit original-response UPDATE/DELETE denial; prototype0007 protects different tables, so LP07 and L4 remain blocked pending separately approved original-owner proof.

T3 historical binding cannot recover unrecorded served catalog generations by archiving today's bytes. Original process/cache/root or immutable release evidence and archive readback are required; browser-local authority remains separate. C3/L4/T3, assembled saved-review → playable-next, H5 recovery rehearsal and hosted merge qualification remain unproved. No live data/service changes, migration, installation or activation occurred; cutover remains **NO-GO**.
