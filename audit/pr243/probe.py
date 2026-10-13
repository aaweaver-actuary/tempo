"""Audit-only observations; no product, timeout, assertion or cleanup changes."""
from pathlib import Path
import hashlib, json, subprocess

fixture = Path('scripts/check_postgres_redis_admission.py')
source = fixture.read_text()
source = source.replace('def validate_restoration(saved, settings, projection, positions):', '''def validate_restoration(saved, settings, projection, positions):
    import sys
    print(json.dumps({'audit': 'pr243_projection_restore', 'identity': saved['identity'],
                      'before': saved['projection_before'], 'installed': saved['projection_installed'],
                      'current': projection}, default=str), file=sys.stderr)''')
assert source != fixture.read_text()
fixture.write_text(source)
# The regular trace retains network bodies; record receipt state after each
# Study case using background GETs only, before leaving the diagnostic evidence.
studies = Path('tests/browser/studies.spec.ts')
source = studies.read_text()
source += '''
// PR243 audit-only observation: preserve accepted operation identity and state.
const auditOperations = new Map<string, string[]>();
test.beforeEach(async ({ page }, testInfo) => {
  const identities: string[] = [];
  auditOperations.set(testInfo.title, identities);
  page.on("response", async response => {
    if (response.request().method() === "POST" && response.url().endsWith("/import/commit")) {
      const body = await response.json().catch(() => null);
      if (typeof body?.operation_id === "string") identities.push(body.operation_id);
      console.log("PR243_IMPORT_ACK", JSON.stringify({title:testInfo.title,status:response.status(),body}));
    }
  });
});
test.afterEach(async ({ request }, testInfo) => {
  for (const identity of auditOperations.get(testInfo.title) ?? []) {
    const response = await request.get(`${api}/operations/${identity}`, {headers:{"X-Tempo-Work-Class":"background"}});
    console.log("PR243_IMPORT_RECEIPT", JSON.stringify({title:testInfo.title,identity,status:response.status(),body:await response.json().catch(()=>null)}));
  }
  auditOperations.delete(testInfo.title);
});
'''
studies.write_text(source)
output = Path('test-results/pr243-focused'); output.mkdir(parents=True, exist_ok=True)
(output/'provenance.json').write_text(json.dumps({
    'source':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
    'audit_definition':str(Path(__file__).resolve()),
    'overlay_sha256':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in [fixture,studies]},
    'purpose':'Observations only: unchanged assertions, product, database state and runner teardown.'
},indent=2)+'\n')
