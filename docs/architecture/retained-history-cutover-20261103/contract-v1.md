# Retained-history contract v1 — #908 W01

Source baseline: `5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a`, schema 59 source
definition. This is a pure wire contract and representative mechanism input,
not a complete migration implementation or an installed authority declaration.
The implementation is `services/learning/runtime/src/tempo_learning_runtime/retained_history_contracts.py`.
The generated JSON schema and two examples are siblings of this document.

## Identity and original representation

Original identity is `(authority, store, complete ordered primary_key)`.
The storage owner supplies the ordered PK column names and canonical codecs in
`StoreShape`, bound to the schema identity and its evidence. Reject missing,
extra, reordered or differently encoded columns. Original BIGINT values use
decimal text; no JavaScript numeric conversion. Identical FEN, response text,
route hash or row count never supplies an identity mapping. Source inventories
may name distinct Content, database and browser authorities without replacing
their original authority. `source_authority` is the primary legacy writer whose
fence binds the final source watermark; other authorities retain distinct
snapshot/journal capture evidence and recovery requirements.

`StateFact` carries original bytes as base64 and SHA-256. Typed JSONB export uses
an owner-versioned codec preserving all typed values; PostgreSQL JSONB does not
promise original input formatting. TEXT serialized FSRS and original immutable
command bytes must remain byte exact. The envelope's sorted-key compact UTF-8
JSON digest is separate from each original byte digest. Hashing never replaces
original retained data. References resolve by original complete keys.

EXACT preserves logical identities, complete records and references. TRANSLATE
requires a separately reviewed versioned bijection with inverse/provenance and
all referencing keys; this DTO does not execute or certify that proof. ARCHIVE
requires immutable readback evidence and is forbidden for required state.
BLOCK retains the actual unresolved identity/reason. An ambiguous required state
blocks its transition and complete activation. Family and store completeness
must be certified by their actual owning capture adapters, including explicit
empty inventories. Count/digest comparisons detect omitted captured records;
they cannot prove that an exporter selected all required source stores.

For schema 057, position state PK is `(repertoire_id,variant,position)`, epochs
are `(repertoire_id,epoch)`, owners are `(repertoire_id)`, seed runs are
`(repertoire_id,owner_epoch)`, seed outcomes are
`(repertoire_id,owner_epoch,variant,position)`, plans `(attempt_id)`, first
responses `(event_id)`, and grades `(effect_id)`. Preserve all unique reference
constraints in addition to PKs. Copy full FSRS, due, internal ID, version, policy,
source digest, owner epoch, donor provenance, reviews and invalidations. Reuse
the original `2^52..2^53-1` internal FSRS ID only after collision preflight against
both existing and future target creators; otherwise BLOCK pending reviewed
mapping. Never replay reviews to derive the original state. One Good per exact
repertoire-position-local-day excludes epoch and policy from its uniqueness.

## Public DTOs and logical boundaries

`SourceManifest`, `TransferPlan`, `StageReceipt`, `VerificationReceipt`,
`FencingProof`, `ActivationReceipt`, `RuntimeProof` are pure frozen records.
`capture_snapshot` belongs to each storage owner; `plan_transfer` only classifies
an already captured manifest. `stage_batch` uses the existing database adapter
and caller-owned transaction: effect + receipt + checkpoint commit together.
`replay_batch` compares complete recorded receipts; the durable adapter looks up
the original `(transfer_id,batch_id)` and returns it for the same input digest,
or rejects changed input. No independent scheduler or writer is introduced.

`verify_staging` must exhaustively inspect real staged records and original
references, including every required hold and accepted obligation. Its receipt
binds the exact manifest and target generation. `activation_blockers` checks
claimed cross-record bindings, including the proposed generation/authority;
it never validates evidence references or grants operational authority. A
representative manifest is always blocked from activation. Complete manifests
require all eight family evidence bindings plus per-store counts/digests.
`activate` belongs to the one Learning adapter under the retained original lock;
`verify_runtime` needs actual native/browser/recovery evidence and artifacts.
Native evidence readers and these effects are not implemented in W01.

## Launcher registration and migration allocation

Existing `scripts/tempo-study-launcher.mjs` accepts only
`graph-preserving-fresh-start` and `freshInitReceipt`; its checks remain intact.
Retained history needs a distinct versioned semantic contract:
`mode=retained-history-v1`, contract version 1, exact source/target authority,
transfer ID, final manifest digest, final verification and fencing proofs,
activation receipt, immutable images, schema map, ingress and original lock.
There is no valid conversion from a fresh-init receipt to retained-history proof.
The normal launcher must reject unsupported retained registrations until the
installed provider is separately implemented and qualified. No switch here.

Coordinator reserves the next Learning migration as
`learning.retained_position/0001`, file `0004_retained_position.sql`, dependent
on `learning.bridge/0001`. W02A alone may propose its additive position tables
and manifest entry after this minimum contract review. No legacy migration is
allocated. W03A/W04A/W09A must request distinct later migrations; no concurrent
manifest edits. Full migration layout remains blocked by unresolved ledger
facts and owner-specific mappings. Independent representative mechanisms may
advance without pretending those UNKNOWNs are settled.

## Proof and limitations

Owning regression `test_retained_history_contracts.py` exercises schema
round-trip, byte retention, PK/codec/order/collision negatives, omitted captured
rows, original batch replay/conflict, warm/final-watermark mismatch, generation
binding, and accepted/uncertain forward recovery. Examples use synthetic
identities and are not valid source-qualified history or scheduling parity.
F01 next-review parity, F10 browser and F13 database fences require their native
owners. No dependency direction, durable writer, startup or transaction changes
occur in W01; architecture impact: none beyond this uninstalled wire contract.
