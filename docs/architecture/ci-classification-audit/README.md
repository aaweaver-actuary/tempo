# Issue 238 frozen CI audit evidence

This branch preserves audit tooling and historical evidence. It is not a production or CI implementation branch. The findings PR links this branch by immutable commit. The corpus cutoff is 2026-10-13 12:18:16 UTC, baseline main c5f773b18c4a5b30a3abda84ecac01ab625a3902.

## Files

- `data/evidence.tar.gz`: frozen raw API responses, retained native plan/layer JSON and digest receipts, logs, policies, all JSON/CSV ledgers and verification results. Its extracted roots are `raw/` and `ledger/`.
- `data/historical.bundle`: 227 captured source/integration/base object identities: 167 supplementary refs, with the remaining 60 already reachable from baseline main. This preserves force-pushed and unmerged audit inputs.
- `data/checksums.json`: SHA-256 and sizes for the archives, exported ledgers and tested tooling.
- CSV exports in `data/` provide direct PR/revision/attempt/job/file/report/opportunity inspection; full nested raw data remains in the archive.
- `data/validation.json`: exact local commands, results, durations and environment. Checks ran on the recorded dirty audit sources before the preservation commit; their content hashes identify the tested files.

Historical artifact ZIPs were digest-checked at retrieval. Necessary JSON members and full failed/checkout logs are retained; unrelated screenshots/traces and full ZIP binaries are not duplicated. Retained plan hashing and snapshot receipts verify the preserved data offline. Hosted artifacts can expire, so fresh independent original-ZIP validation may become unavailable; the audit never treats expiration as a passing result.

## Offline reproduction

Use an isolated clone of this evidence branch inside `.dev-copies`, never the live study checkout. It needs Python standard library, Node and Git; no Tempo dependencies, database, Docker stack or Python virtual environment is needed for accounting.

From the isolated repository root:

```sh
mkdir -p evidence
python3 - <<'PYCODE'
import hashlib,json
from pathlib import Path
root=Path('docs/architecture/ci-classification-audit')
for item in json.loads((root/'data/checksums.json').read_text())['files']:
    target=root/item['path']
    assert hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256'], target
PYCODE
tar -xzf docs/architecture/ci-classification-audit/data/evidence.tar.gz -C evidence
git bundle verify docs/architecture/ci-classification-audit/data/historical.bundle
git fetch docs/architecture/ci-classification-audit/data/historical.bundle 'refs/audit-candidate/*:refs/audit-candidate/*'
python3 docs/architecture/ci-classification-audit/audit.py analyze --evidence evidence/raw --output evidence/ledger
python3 docs/architecture/ci-classification-audit/recover.py --evidence evidence/raw --output evidence/ledger
python3 docs/architecture/ci-classification-audit/enrich.py --evidence evidence/raw --output evidence/ledger
python3 docs/architecture/ci-classification-audit/diagnose.py --evidence evidence/raw --output evidence/ledger
python3 docs/architecture/ci-classification-audit/opportunities.py --output evidence/ledger
python3 docs/architecture/ci-classification-audit/verify.py --output evidence/ledger
python3 -m unittest discover -s docs/architecture/ci-classification-audit -p test_audit.py -v
```

The frozen input snapshots are `manifest.json`, `runs.json`, retained `artifacts/`, `logs/` and `raw/`; analyze overwrites only derived ledgers. Recovery binds each checkout to its own run attempt and keeps unknown plans unknown. Re-running recovery is idempotent. Source-plan insertion order must be preserved because native plan hashes use JavaScript JSON serialization.

`opportunities.py` intentionally encodes the reviewed #238 PR/case/run selections. A different cohort requires a new reviewed case selection; do not apply those frozen opportunity assumptions automatically.

A new online collection requires authenticated `gh`, authorized repository/Actions access, and a **new** evidence/output directory and cutoff:

```sh
python3 docs/architecture/ci-classification-audit/audit.py collect --cutoff YYYY-MM-DDTHH:MM:SSZ --evidence NEW_EVIDENCE/raw --output NEW_EVIDENCE/ledger
python3 docs/architecture/ci-classification-audit/audit.py analyze --evidence NEW_EVIDENCE/raw --output NEW_EVIDENCE/ledger
python3 docs/architecture/ci-classification-audit/recover.py --evidence NEW_EVIDENCE/raw --output NEW_EVIDENCE/ledger
python3 docs/architecture/ci-classification-audit/enrich.py --fetch --evidence NEW_EVIDENCE/raw --output NEW_EVIDENCE/ledger
python3 docs/architecture/ci-classification-audit/diagnose.py --evidence NEW_EVIDENCE/raw --output NEW_EVIDENCE/ledger
python3 docs/architecture/ci-classification-audit/verify.py --output NEW_EVIDENCE/ledger
```

Source discovery is by PR creation time, file snapshot and unique branch/native-plan association. A fresh historical API collection cannot recover expired/missing plans or force-push history by substituting current metadata. Save all such gaps. The audit itself does not request workflow reruns or modify GitHub settings.

## Metric boundaries

Runner consumption sums unique assigned jobs across attempts, including cancellation, and censors at the cutoff. Elapsed metrics use terminal job/gate timestamps and interval unions. Summing parallel jobs is never a waiting-time estimate. Archived PR file/commit/state snapshots are retrieval metadata; exact historical paths use captured base/head and Git or verified checkout parents. Unknown tiers/bases remain null with explicit gaps.

Opportunity envelopes use actual affected job times. Exact-key repetition is a screen, not original-evidence eligibility certification. Classification and test ownership share an envelope; the combined total deduplicates job IDs. Dependency-path scenarios retain observed scheduling slack and cannot predict future queue time or human waiting. Original workflows, runtime, inventories, commands, source association, artifacts and newer matching failures still govern safe reuse.
