import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createExecutor} from './release.mjs';

const monotonic = () => Number(process.hrtime.bigint()) / 1e6;
const print = event => console.log(JSON.stringify(event));

// One private executor seam. The controller owns ordering, gates and outcomes;
// the retained-wrapper adapter owns processes, runtime calls and persistence.
export async function prepareSession(options, {execute = createExecutor(), clock = monotonic, output = print} = {}) {
  const start = clock();
  let prepared;
  try { prepared = await execute('prepare', options); }
  catch (error) { const end = clock(); output({event:'phase',phase:'preparation',start,end,elapsed_ms:end-start,outcome:'failed'}); throw error; }
  prepared.timings = [{phase:'preparation', start, end:clock(), outcome:'passed'}];
  prepared.timings[0].elapsed_ms = prepared.timings[0].end - start;
  await execute('save-prepared', prepared);
  output({event:'prepared', revision:prepared.revision, evidence:prepared.evidence, timing:prepared.timings[0]});
  return prepared;
}

function verifiedAvailability(ready, receipt, requestedRevision, previous = false) {
  assert.equal(ready.verified, true, 'Readiness is unverified');
  assert.match(ready.revision, /^[a-f0-9]{40}$/);
  assert.ok(new URL(ready.url).origin, 'Missing actual application URL');
  assert.equal(receipt.revision, ready.revision, 'Receipt differs from running revision');
  assert.ok(receipt.verified_at && receipt.images && Object.keys(receipt.images).length, 'Incomplete deployment receipt');
  assert.equal(ready.receiptVerified, true, 'Authoritative receipt was not verified');
  if (!previous) assert.equal(ready.revision, requestedRevision, 'Requested image identity differs');
  return {event:'available', status:previous ? 'previous_verified_revision_available_update_deferred' : 'requested_revision_installed_and_ready',
    actualRevision:ready.revision, requestedRevision, url:ready.url, requestedInstalled:!previous,
    ...receipt.evidence?.release_services};
}

export async function runSession(prepared, {execute = createExecutor(), clock = monotonic, output = print, signal} = {}) {
  const context = {...prepared, signal};
  const result = {availability:null, installed:false, handback:'not-acquired', allocationRolledBack:false, closeoutComplete:false,
    exitCode:0, failure:null, cleanupFailures:[], timings:[...(prepared.timings ?? [])]};
  let allocation, allocationAttempted = false, requiredFailed = false;
  const timed = async (phase, action, required = true) => {
    const timing = {phase, start:clock(), outcome:required ? 'running' : 'not-required'};
    result.timings.push(timing);
    try { const value = required ? await action() : undefined; if (required) timing.outcome = value?.complete === false ? 'incomplete' : 'passed'; return value; }
    catch (error) { timing.outcome = 'failed'; throw error; }
    finally { timing.end = clock(); timing.elapsed_ms = timing.end - timing.start; output({event:'phase', ...timing}); }
  };
  const call = async (name, payload) => { signal?.throwIfAborted(); return execute(name, context, payload); };
  try {
    const preflight = await call('preflight');
    allocationAttempted = true;
    allocation = await timed('resource wait', async () => {
      const value = await call('allocation'); assert.equal(value.validated,true,'Current allocation was not validated'); return value;
    });
    result.handback = 'pending';
    await call('revalidate');
    if (preflight.deferred) {
      for (const phase of ['build','artifact verification','shutdown','backup/restore/verification','migration'])
        await timed(phase, null, false);
      const previous = await timed('activation/readiness', () => call('verify-previous'));
      result.availability = verifiedAvailability(previous.ready, previous.receipt, context.revision, true);
      output(result.availability);
      await timed('post-readiness checks', null, false);
    } else {
      await timed('build', () => call('build', allocation));
      await timed('artifact verification', async () => {
        await call('verify-artifacts', allocation);
        await call('settle-builder');
      });
      const facts = await timed('shutdown', async () => {
        const inspected = await call('inspect');
        assert.equal(inspected.migrationRecovery, false, 'Migration retry needs separate authorization');
        await call('revalidate');
        await call('quiet');
        await call('stop');
        return inspected;
      });
      await timed('backup/restore/verification', async () => {
        await call('checkpoint-before');
        const backup = await call('backup');
        assert.ok(backup.verified && backup.restore_removed, 'Backup/restore verification incomplete');
      }, facts.pendingMigrations);
      await timed('migration', async () => {
        await call('migrate');
        await call('verify-migration');
      }, facts.pendingMigrations);
      const installed = await timed('activation/readiness', async () => {
        await call('activate');
        const ready = await call('verify-ready');
        assert.equal(ready.verified, true, 'Readiness failed');
        const receipt = await call('commit');
        return {ready:{...ready, receiptVerified:true}, receipt};
      });
      result.availability = verifiedAvailability(installed.ready, installed.receipt, context.revision);
      result.installed = true;
      output(result.availability);
      await timed('post-readiness checks', () => call('installed-checks'));
    }
  } catch (error) {
    requiredFailed = !result.availability;
    let first = error;
    while (first instanceof AggregateError && first.errors.length) {
      result.cleanupFailures.push(...first.errors.slice(1)); first = first.errors[0];
    }
    result.failure = first;
    result.exitCode = Number.isInteger(first.status) && first.status > 0 ? first.status : signal?.aborted ? 130 : 1;
  } finally {
    await timed('handback/closeout', async () => {
      let clean = false;
      try {
        const cleanup = await execute('cleanup', context, {allocation, requiredFailed, available:Boolean(result.availability),failure:result.failure});
        clean = cleanup.complete === true;
        if (!allocation && cleanup.allocationRolledBack === true) {
          assert.equal(cleanup.claimAbsent,true,'Rolled-back allocation still has an ownership claim');
          result.allocationRolledBack = true; result.handback = 'complete';
        }
        if (!allocation && allocationAttempted && cleanup.claimAbsent !== true) result.handback = 'incomplete';
        if (!clean) throw new Error(cleanup.reason ?? 'Owned cleanup is unverified; retain ownership evidence');
      } catch (error) { result.cleanupFailures.push(error); }
      if (allocation && clean) {
        try {
          const handback = await execute('handback', context, allocation);
          result.handback = handback.complete ? 'complete' : 'awaiting_owner';
        } catch (error) { result.handback = 'incomplete'; result.cleanupFailures.push(error); }
      } else if (allocation) result.handback = 'incomplete';
      result.closeoutComplete = clean && (result.handback === 'not-acquired' || result.handback === 'complete') && !result.failure;
      if (!result.exitCode && !result.closeoutComplete) result.exitCode = 3;
      return {complete:clean && (result.handback === 'not-acquired' || result.handback === 'complete')};
    });
    try { await execute('closeout', context, result); }
    catch (error) { result.cleanupFailures.push(error); result.closeoutComplete = false; result.exitCode ||= 1; }
    output({event:'closeout', installed:result.installed, availability:result.availability,
      handback:result.handback, allocationRolledBack:result.allocationRolledBack, complete:result.closeoutComplete, exitCode:result.exitCode,
      failure:result.failure?.message, cleanupFailures:result.cleanupFailures.map(String)});
  }
  return result;
}

async function main() {
  const {values} = parseArgs({options:{prepare:{type:'boolean'}, run:{type:'string'}, source:{type:'string'}, revision:{type:'string'},
    registration:{type:'string'}, state:{type:'string'}, evidence:{type:'string'}, 'build-manifest':{type:'string'},
    'build-cpus':{type:'string'}, 'build-memory-mib':{type:'string'}, 'helper-cpus':{type:'string'},
    'minimum-free-bytes':{type:'string'}, 'acquire-budget':{type:'boolean'}, 'budget-directory':{type:'string'},
    'installed-timeout-ms':{type:'string'}}});
  assert.notEqual(Boolean(values.prepare), Boolean(values.run), 'Choose --prepare or --run <prepared-session.json>');
  const controller = new AbortController();
  const cancel = () => controller.abort(new Error('Release session cancelled'));
  process.once('SIGINT', cancel); process.once('SIGTERM', cancel);
  try {
    if (values.prepare) await prepareSession({...values, signal:controller.signal});
    else {
      const prepared = JSON.parse(readFileSync(resolve(values.run), 'utf8'));
      // Permission and allocation are invocation inputs, never saved grants.
      prepared.acquireBudget = Boolean(values['acquire-budget']);
      prepared.budgetDirectory = values['budget-directory'] ?? process.env.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY;
      prepared.limits = {cpus:Number(values['build-cpus']), memoryMiB:Number(values['build-memory-mib'])};
      prepared.helperCpus = values['helper-cpus'] === undefined ? undefined : Number(values['helper-cpus']);
      const result = await runSession(prepared, {signal:controller.signal});
      process.exitCode = result.exitCode;
    }
  } finally { process.off('SIGINT', cancel); process.off('SIGTERM', cancel); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { console.error(error.stack ?? error); process.exitCode = error.status || 1; });
