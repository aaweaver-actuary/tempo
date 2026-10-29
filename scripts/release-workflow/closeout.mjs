import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {join} from 'node:path';

// The caller has already committed all required deployment metadata. These
// checks and evidence attachments must never delay the availability event.
export async function installedChecks(context, run, wrapperDirectory) {
  await run('python3',[join(wrapperDirectory,'verify-installed.py'),context.evidence],
    {timeout:context.installedTimeoutMs,echo:true});
  await run(process.execPath,[join(wrapperDirectory,'verify-ui-fix.mjs'),context.evidence],
    {timeout:context.installedTimeoutMs,echo:true});
}

export function attachInstalledEvidence(context, deployment) {
  if (context.committed) {
    const path = join(context.state,'deployment.json');
    const record = JSON.parse(readFileSync(path,'utf8'));
    assert.deepEqual(record,context.committed,'Concurrent deployment change before evidence attachment');
    for (const [key,name] of [['installed_recovery','installed-recovery.json'],['installed_ui_fix','installed-ui-fix.json'],['installed_preservation','installed-preservation.json']])
      if (existsSync(join(context.evidence,name))) record.evidence[key] = join(context.evidence,name);
    // Required installation facts were committed before availability. Only
    // references to completed post-readiness evidence are added here.
    deployment.atomicJson(path,record);
    context.committed = record;
  }
}

export function closeout(context, result) {
  context.save('closeout.json', {revision:context.revision,availability:result.availability,
    installed:result.installed,handback:result.handback,complete:result.closeoutComplete,exit_code:result.exitCode,
    failure:result.failure ? {message:result.failure.message,status:result.failure.status,stack:result.failure.stack,
      stdout:result.failure.stdout,stderr:result.failure.stderr} : null,
    cleanup_failures:result.cleanupFailures.map(String),timings:result.timings,
    completed_at:new Date().toISOString()});
}
