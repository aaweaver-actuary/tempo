import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, statfsSync, writeFileSync} from 'node:fs';
import {join, resolve, relative, isAbsolute, basename} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {prepareBuild, prepareReleaseServices, buildCandidate, verifyArtifacts} from './build.mjs';
import {installedChecks, closeout, attachInstalledEvidence} from './closeout.mjs';

const wrapperDirectory = fileURLToPath(new URL('.', import.meta.url));
const checkpointCode = "import os,json,psycopg\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL'],options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=100') as db:\n result=db.execute(\"SELECT json_build_object(\\n 'ledger',(SELECT json_agg(version ORDER BY version) FROM tempo_schema_migrations),\\n 'settings_digest',(SELECT md5(to_jsonb(s)::text) FROM settings s WHERE id=1),\\n 'study_only_mode',(SELECT study_only_mode FROM settings WHERE id=1),\\n 'cards',(SELECT json_build_object('count',count(*),'ids_digest',md5(string_agg(id,'' ORDER BY id))) FROM cards),\\n 'reviews',(SELECT json_build_object('count',count(*),'rows_digest',md5(string_agg(md5(to_jsonb(r)::text),'' ORDER BY id))) FROM reviews r),\\n 'review_receipts',(SELECT json_build_object('count',count(*),'rows_digest',md5(string_agg(md5(to_jsonb(r)::text),'' ORDER BY attempt_id))) FROM review_attempt_receipts r),\\n 'protected_attempts',(SELECT COALESCE(json_agg(row_to_json(z)),'[]'::json) FROM (\\n   SELECT o.queue_entry_id,o.card_id,o.revision,\\n   md5((to_jsonb(o)-ARRAY['attempt_failed','last_status','review_result_json'])::text) AS identity_hash\\n   FROM queue_attempt_origins o JOIN daily_queue q ON q.id=o.queue_entry_id\\n   WHERE (q.status='queued' AND o.issued_as_head=1) ORDER BY o.queue_entry_id,o.card_id,o.revision LIMIT 10001) z),\\n 'pending_operations',(SELECT COALESCE(json_agg(row_to_json(z)),'[]'::json) FROM (\\n   SELECT operation_id,command_name,request_hash,md5(COALESCE(payload_json,'')) AS payload_hash,state\\n   FROM operation_receipts WHERE state='pending' ORDER BY operation_id LIMIT 10001) z),\\n 'feedback',(SELECT COALESCE(json_agg(row_to_json(z)),'[]'::json) FROM (\\n   SELECT effect_key,payload_hash,card_id,card_revision,md5(identity_json) AS identity_hash\\n   FROM study_game_feedback ORDER BY effect_key LIMIT 10001) z)\\n);\").fetchone()[0]\n for key,table,order in [('all_queue','daily_queue','id'),('all_attempt_origins','queue_attempt_origins','queue_entry_id,card_id,revision'),('cards_full','cards','id')]:\n  result[key]=db.execute(\"SELECT json_build_object('count',count(*),'digest',md5(string_agg(md5(to_jsonb(r)::text),'' ORDER BY \"+order+\"))) FROM \"+table+\" r\").fetchone()[0]\n print(json.dumps(result))\n";
const queueProbeCode = "import sys,os,json,psycopg\nfrom datetime import date\nsys.path[:0]=['/source/database','/source','/app']\nos.environ['TEMPO_DATABASE_READ_URL']='postgresql://tempo_reader@postgres:5432/tempo'\nfrom tempo_legacy_database.adapters.postgres_store import PostgresConnection,tempo_row_factory\nfrom tempo_legacy_database.adapters.http_composition.queue_projection import queue_rows,issued_head\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL'],row_factory=tempo_row_factory,options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=100') as native:\n db=PostgresConnection(native,read_only=True)\n rows=queue_rows(db,date.today().isoformat(),1,miss_reason='Priority review \u00b7 missed in a recent game')\n if not rows:print(json.dumps({'empty':True}))\n else:\n  r=rows[0];origin=issued_head(db,r['queue_entry_id'],r['id'],r['revision'])\n  print(json.dumps({'empty':False,'queue_entry_id':r['queue_entry_id'],'card_id':r['id'],'revision':r['revision'],'issued_as_head':bool(origin and origin['issued_as_head'])}))\n";
const postPreservationCode = "\"\"\"One read-only post-install check; no review or queue command.\"\"\"\nimport json\nimport os\nimport sys\nimport psycopg\n\nbaseline = json.loads(sys.argv[1])\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL'], options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=100') as db:\n    for old in baseline['protected_attempts']:\n        row = db.execute(\"SELECT md5((to_jsonb(o)-ARRAY['attempt_failed','last_status','review_result_json'])::text) FROM queue_attempt_origins o WHERE queue_entry_id=%s AND card_id=%s AND revision=%s\", (old['queue_entry_id'], old['card_id'], old['revision'])).fetchone()\n        assert row and row[0] == old['identity_hash'], 'Protected attempt identity changed'\n    for old in baseline['feedback']:\n        row = db.execute('SELECT payload_hash,card_id,card_revision,md5(identity_json) FROM study_game_feedback WHERE effect_key=%s', (old['effect_key'],)).fetchone()\n        assert row and tuple(row) == (old['payload_hash'], old['card_id'], old['card_revision'], old['identity_hash']), 'Priority effect identity changed'\n    print(json.dumps({'protected_attempt_identities_preserved': len(baseline['protected_attempts']), 'priority_effect_identities_preserved': len(baseline['feedback']), 'read_only': True, 'reviews': db.execute('SELECT count(*) FROM reviews').fetchone()[0], 'review_receipts': db.execute('SELECT count(*) FROM review_attempt_receipts').fetchone()[0]}))\n";
const read = path => JSON.parse(readFileSync(path,'utf8'));
const inside = (path, root) => { const part = relative(root,path); return !part || !part.startsWith('..') && !isAbsolute(part); };
function requireAbsentLane(directory,name) {
  try { lstatSync(join(directory,name)); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new Error(name+' remains; retain ownership');
}

function processRun(command, args, options = {}) {
  return new Promise((accept,reject) => {
    const child = spawn(command,args,{cwd:options.cwd, env:process.env, stdio:['ignore','pipe','pipe'],
      signal:options.signal, timeout:options.timeout ?? 0});
    let stdout = '', stderr = '', processError;
    child.stdout.on('data', chunk => { stdout += chunk; if (options.echo) process.stdout.write(chunk); });
    child.stderr.on('data', chunk => { stderr += chunk; if (options.echo) process.stderr.write(chunk); });
    child.once('error', error => { processError = error; if (!child.pid) reject(error); });
    child.once('close', (code,signal) => {
      if (processError) { Object.assign(processError,{stdout,stderr}); reject(processError); return; }
      if (code === 0 || options.allowFailure) accept({code,stdout,stderr,signal});
      else { const error = new Error(`${command} failed (${code ?? signal}): ${(stderr || stdout).slice(-4000)}`);
        Object.assign(error,{status:code,stdout,stderr,signal}); reject(error); }
    });
  });
}

export function createExecutor({processRunner = processRun, moduleLoader = url => import(url)} = {}) {
  let c, deployment, budget, runtimeModule, runtime, releaseLock, loan, before, hashes, originallyRunning;
  let migrationStarted = false, activationStarted = false, stopped = false, helperCounter = 0, cleanupMode = false;
  let allocationRolledBack = false, allocationRollbackFailure;
  const helpers = new Map();
  const run = (command,args,options = {}) => processRunner(command,args,{cwd:c?.source,
    signal:cleanupMode ? undefined : c?.signal, ...options});
  const docker = (args, options) => run('docker',['--context',c.target.context,...args], options);
  async function modules() {
    deployment ??= await moduleLoader(pathToFileURL(join(c.source,'scripts/tempo-deployment.mjs')));
    budget ??= await moduleLoader(pathToFileURL(join(c.source,'scripts/development-budget.mjs')));
    runtimeModule ??= await moduleLoader(pathToFileURL(join(c.source,'scripts/tempo-runtime.mjs')));
    c.save = (name,value) => deployment.atomicJson(join(c.evidence,name),value);
    c.load = name => read(join(c.evidence,name));
  }
  async function sourceFence(remote = true) {
    assert.equal((await run('git',['rev-parse','HEAD'])).stdout.trim(), c.revision, 'Source revision changed');
    assert.equal((await run('git',['status','--porcelain','--untracked-files=all'])).stdout.trim(), '', 'Release source is dirty');
    const origin = (await run('git',['remote','get-url','origin'])).stdout.trim();
    assert.match(origin,/^(?:https?:\/\/github\.com\/|git@github\.com:)aaweaver-actuary\/tempo(?:\.git)?$/);
    if (remote) {
      const main = (await run('git',['ls-remote',origin,'refs/heads/main'],{timeout:15000})).stdout.trim().split(/\s+/)[0];
      assert.equal(main,c.revision,'Selected revision is no longer current main; prepare a new session');
    }
  }
  async function revalidate(remote = true) {
    await sourceFence(remote);
    assert.equal(readFileSync(c.registrationPath,'utf8'),c.registrationText,'Registration changed');
    assert.equal(readFileSync(join(c.state,'deployment.json'),'utf8'),c.deploymentText,'Concurrent deployment drift');
    assert.equal(readFileSync(c.buildManifestPath,'utf8'),c.buildManifestText,'Build manifest changed');
    for (const [path,text] of Object.entries(c.previousInputs)) assert.equal(readFileSync(path,'utf8'),text,'Saved deployment input changed: '+path);
    c.target = JSON.parse(c.registrationText);
    c.previous = JSON.parse(c.deploymentText);
    c.oldConfig = read(c.previous.composeFiles[0]);
    const validated = await prepareReleaseServices(c,run);
    assert.deepEqual(validated.backupInputs,c.backupInputs,'Selected backup configuration inputs changed; prepare a new session');
    c.servicePlan = validated.servicePlan;
    if (c.ownerToken) await budget.validateDevelopmentBudgetOwner({directory:c.budgetDirectory,token:c.ownerToken});
  }
  async function absentOrOwned(name) {
    const result = await docker(['inspect',name],{allowFailure:true,timeout:5000});
    if (result.code !== 0) {
      assert.equal(result.code,1,'Helper absence probe failed');
      assert.match(result.stderr, /No such (?:object|container)/i, 'Helper absence is uncertain');
      assert.ok(result.stderr.includes(name),'Absence probe did not identify the helper');
      return null;
    }
    const [container] = JSON.parse(result.stdout);
    assert.equal(container.Name,'/'+name,'Helper name mismatch');
    assert.equal(container.Config.Labels['tempo.resource-owner'],c.ownerToken,'Helper owner mismatch');
    assert.equal(container.Config.Labels['tempo.release.session'],c.runId,'Helper session mismatch');
    const saved = helpers.get(name);
    if (saved?.id) assert.equal(container.Id,saved.id,'Helper identity changed');
    if (saved) saved.id = container.Id;
    return container;
  }
  async function helper(commandArgs, composeService, options = {}) {
    const name = `${c.target.project}-release-${c.runId.slice(0,8)}-${++helperCounter}`;
    const key = 'release-helper:'+name;
    helpers.set(name,{key});
    await budget.recordBudgetResource(key,true,c.ownerToken,c.budgetDirectory);
    c.save('resource-cleanup.json',{complete:false,helpers:Object.fromEntries(helpers)});
    const args = [...commandArgs];
    const index = composeService ? args.indexOf(composeService,args.indexOf('run')) : args.indexOf('run')+1;
    args.splice(index,0,'--name',name,'--label','tempo.resource-owner='+c.ownerToken,'--label','tempo.release.session='+c.runId,
      ...(composeService === 'migration' ? ['--workdir','/source'] : []));
    const result = await docker(args,options);
    assert.equal(await absentOrOwned(name),null,'Auto-removed helper is still present');
    await budget.recordBudgetResource(key,false,c.ownerToken,c.budgetDirectory);
    helpers.delete(name);
    return result;
  }
  const runtimeRun = async (command,args,options = {}) => {
    assert.equal(command,'docker','Unexpected runtime transport');
    // createRuntime already puts --context first; helper() owns that prefix.
    const adjusted = [...args];
    assert.equal(adjusted.shift(),'--context'); assert.equal(adjusted.shift(),c.target.context);
    const service = adjusted.includes('compose') && adjusted.includes('run')
      ? ['migration','postgres-backup'].find(name => adjusted.indexOf(name)>adjusted.indexOf('run')) : null;
    if (service) {
      if (c.helperCpus !== undefined) adjusted.splice(adjusted.indexOf('run'),0,'-f',join(c.evidence,'helper-cpu-only.json'));
      c.helperAllowances ??= [];
      c.helperAllowances.push({at:new Date().toISOString(),service,temporary_cpus:c.helperCpus ?? c.oldConfig.services[service].cpus,
        original_cpus:c.oldConfig.services[service].cpus,memory_unchanged:true,auto_remove:adjusted.includes('--rm')});
      c.save('helper-allowances.json',c.helperAllowances);
      return helper(adjusted,service,options);
    }
    if (adjusted[0] === 'run') {
      adjusted.splice(1,0,'--cpus',String(Math.min(c.limits.cpus,1)),'--memory',Math.min(c.limits.memoryMiB,256)+'m','--pids-limit','64');
      return helper(adjusted);
    }
    return docker(adjusted,options);
  };
  async function inventory() {
    const ids = (await docker(['ps','-aq','--filter','label=com.docker.compose.project='+c.target.project])).stdout.trim().split(/\s+/).filter(Boolean);
    assert.ok(ids.length,'Registered target has no containers');
    const containers = JSON.parse((await docker(['inspect',...ids])).stdout);
    const rows = containers.filter(row => row.Config.Labels['com.docker.compose.oneoff'] !== 'True');
    const services = rows.map(row => row.Config.Labels['com.docker.compose.service']);
    assert.equal(new Set(services).size,services.length,'Duplicate target service');
    return Object.fromEntries(rows.map(row => [row.Config.Labels['com.docker.compose.service'],row]));
  }
  function makeRuntime(previous = false) {
    const target = {...c.target,composeFiles:previous ? c.previous.composeFiles : [join(c.evidence,'compose-release.json')]};
    return runtimeModule.createRuntime(target,{run:runtimeRun,stateDirectory:c.state,previous:c.previous,
      revision:previous ? c.previous.revision : c.revision,fallback:previous,log:console.log});
  }
  async function checkpoint(name) {
    const result = JSON.parse((await runtime.compose(['--profile','maintenance','run','--rm','--no-deps','-T','migration','-c',checkpointCode])).stdout);
    c.save(name,result); return result;
  }
  async function checkReadyIdentity() {
    await runtime.verifyReady();
    const final = await inventory();
    for (const service of c.servicePlan.preservedServices) {
      assert.equal(final[service].Id,before[service].Id,'Unexpected recreation: '+service);
      assert.equal(final[service].Image,before[service].Image);
      assert.equal(final[service].Config.Labels['com.docker.compose.config-hash'],before[service].Config.Labels['com.docker.compose.config-hash']);
    }
    for (const service of c.servicePlan.updatedServices.filter(name => name !== 'postgres-backup' && originallyRunning.includes(name))) {
      assert.equal(final[service].Image,c.images[service],'Wrong running image: '+service);
      assert.equal(final[service].Config.Labels['com.docker.compose.config-hash'],hashes.get(service));
      assert.ok(!final[service].Mounts.some(mount => ['/app','/app/app'].includes(mount.Destination)),'Serving source overlay');
    }
    if (c.servicePlan.backup.changed) {
      const backup = final['postgres-backup'];
      assert.ok(backup?.State?.Running,'Updated backup is not running');
      assert.notEqual(backup.Id,before['postgres-backup'].Id,'Backup configuration change did not replace the original container');
      assert.equal(backup.Image,c.backupInputs.image,'Backup immutable image changed');
      assert.equal(backup.Config.Labels['com.docker.compose.config-hash'],hashes.get('postgres-backup'),'Backup configuration was not applied');
      assert.equal(backup.Config.StopSignal || c.backupInputs.imageStopSignal,
        c.servicePlan.backup.stopSignal.present ? c.servicePlan.backup.stopSignal.value : c.backupInputs.imageStopSignal,'Backup stop signal differs');
    }
    c.save('containers-after.json',final);
    return {verified:true,revision:c.revision,url:c.target.webUrl};
  }
  return async function execute(step, context, payload) {
    c = context;
    if (step === 'prepare') {
      for (const name of ['source','revision','registration','state','evidence','build-manifest','minimum-free-bytes']) assert.ok(context[name],'Missing --'+name);
      assert.match(context.revision,/^[a-f0-9]{40}$/);
      const prepared = {source:resolve(context.source),revision:context.revision,registrationPath:resolve(context.registration),state:resolve(context.state),
        evidence:resolve(context.evidence),buildManifestPath:resolve(context['build-manifest']),runId:randomUUID(),
        minimumFreeBytes:Number(context['minimum-free-bytes']),installedTimeoutMs:Number(context['installed-timeout-ms'] ?? 120000),signal:context.signal};
      c = prepared;
      assert.ok(Number.isSafeInteger(c.minimumFreeBytes) && c.minimumFreeBytes>0,'Invalid backup reserve');
      assert.ok(Number.isSafeInteger(c.installedTimeoutMs) && c.installedTimeoutMs>0 && c.installedTimeoutMs<=300000,'Installed checks need a finite timeout <=300000ms');
      c.registrationText = readFileSync(c.registrationPath,'utf8'); c.target = JSON.parse(c.registrationText);
      assert.equal(c.target.version,1,'This retained wrapper serves the version-1 installation only');
      assert.ok(!inside(c.evidence,c.source) && !inside(c.evidence,c.target.root) && !inside(c.evidence,c.state),'Evidence must be outside source and live installation state');
      assert.notEqual(c.source,resolve(c.target.root),'Do not build from the live Study checkout');
      assert.ok(!existsSync(c.evidence) || !readdirSync(c.evidence).length,'Evidence directory is not empty; preserve it');
      await sourceFence(); await modules();
      assert.equal(basename(c.state),deployment.targetKey(c.target),'State directory does not match the registered target');
      assert.ok(existsSync(join(c.target.root,'node_modules/playwright/index.mjs')),'Installed UI check requires existing Playwright at registered checkout node_modules/playwright/index.mjs');
      const assessment = await deployment.assessMainVerification(c.revision,undefined,{signal:c.signal});
      assert.equal(assessment.status,'verified',assessment.message); c.ciEvidence = assessment.evidence;
      c.deploymentText = readFileSync(join(c.state,'deployment.json'),'utf8'); c.previous = JSON.parse(c.deploymentText);
      assert.equal(c.previous.composeFiles.length,1,'Retained wrapper supports one frozen complete Compose file; do not flatten unknown overlays');
      c.previousInputs = Object.fromEntries([...c.previous.composeFiles,...(c.previous.imageOverride ? [c.previous.imageOverride] : [])]
        .map(path => [path,readFileSync(path,'utf8')]));
      c.oldConfig = JSON.parse(c.previousInputs[c.previous.composeFiles[0]]);
      c.buildManifestText = readFileSync(c.buildManifestPath,'utf8');
      // Read-only selected-source configuration, local image identity and Bake resolution.
      Object.assign(c,await prepareReleaseServices(c,run));
      Object.assign(c,await prepareBuild(c,run));
      c.preparedInventory = await inventory();
      mkdirSync(c.evidence,{recursive:true,mode:0o700});
      writeFileSync(join(c.evidence,'build.json'),c.buildManifestText,{mode:0o600,flag:'wx'});
      c.save('before-deployment.json',c.previous);
      c.save('runtime-preflight.json',{services:Object.fromEntries(Object.entries(c.preparedInventory).map(([s,v]) => [s,{id:v.Id,image:v.Image}]))});
      delete c.signal; return c;
    }
    await modules();
    if (step === 'save-prepared') { const {save,load,...serializable} = c; c.save('prepared-session.json',serializable); return; }
    if (step === 'preflight') {
      await revalidate();
      const verification = await deployment.assessMainVerification(c.revision,undefined,{signal:c.signal});
      if (verification.status !== 'verified') return {deferred:true,reason:verification.message};
      c.ciEvidence = verification.evidence; return {deferred:false};
    }
    if (step === 'revalidate') return revalidate();
    if (step === 'allocation') {
      assert.ok(Number.isFinite(c.limits.cpus) && c.limits.cpus>0 && Number.isSafeInteger(c.limits.memoryMiB) && c.limits.memoryMiB>0,'Supply current --build-cpus and --build-memory-mib');
      if (c.helperCpus !== undefined) assert.ok(Number.isFinite(c.helperCpus) && c.helperCpus>0,'Invalid separately authorized helper CPU allowance');
      c.budgetDirectory ??= process.env.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY ?? join(tmpdir(),'tempo-development-resource-budget-v1.lock');
      c.ownerToken = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
      let acquiredHere = false;
      if (!c.ownerToken) {
        assert.equal(c.acquireBudget,true,'No current owner token; obtaining a new loan requires explicit --acquire-budget authorization');
        loan = await budget.acquireDevelopmentBudget({directory:c.budgetDirectory,signal:c.signal}); c.ownerToken = loan.token; acquiredHere = true;
      }
      try {
        await budget.validateDevelopmentBudgetOwner({directory:c.budgetDirectory,token:c.ownerToken});
        c.existingResources = Object.fromEntries(readdirSync(join(c.budgetDirectory,'resources')).map(name => [name,readFileSync(join(c.budgetDirectory,'resources',name),'utf8')]));
        requireAbsentLane(c.budgetDirectory,'builder-lane');
        requireAbsentLane(c.budgetDirectory,'admission-lane');
        if (c.helperCpus !== undefined) c.save('helper-cpu-only.json',{services:Object.fromEntries(['migration','postgres-backup'].map(s => [s,{cpus:c.helperCpus}]))});
        return {validated:true,owned:Boolean(loan),limits:c.limits,manifest:c.buildManifest};
      } catch (error) {
        if (acquiredHere) {
          try {
            const owner = await budget.validateDevelopmentBudgetOwner({directory:c.budgetDirectory,token:loan.token});
            assert.equal(owner.pid,process.pid,'Self-acquired lease process ownership changed');
            assert.equal(owner.token,loan.token,'Self-acquired lease token changed');
            assert.equal(loan.directory,c.budgetDirectory,'Self-acquired lease directory changed');
            requireAbsentLane(c.budgetDirectory,'builder-lane');
            requireAbsentLane(c.budgetDirectory,'admission-lane');
            assert.deepEqual(readdirSync(join(c.budgetDirectory,'resources')),[],'Resource claims remain after failed allocation');
            // No build/admission execution has been issued before allocation returns.
            await loan.release();
            loan = undefined; delete c.ownerToken; allocationRolledBack = true;
          } catch (rollbackError) {
            allocationRollbackFailure = rollbackError;
            throw new AggregateError([error,rollbackError],'Allocation failed; ownership rollback is incomplete');
          }
        }
        throw error;
      }
    }
    if (step === 'build') return buildCandidate(c,budget,run);
    if (step === 'verify-artifacts') return verifyArtifacts(c,async (name,image,entrypoint,args,memory) =>
      helper(['run','--rm','--network','none','--read-only','--cpus',String(Math.min(c.limits.cpus,1)),
        '--memory',Math.min(c.limits.memoryMiB,memory)+'m','--pids-limit','64',...entrypoint,image,...args]));
    if (step === 'settle-builder') {
      assert.ok(!existsSync(join(c.budgetDirectory,'builder-lane')),'Builder lane remains; retain ownership');
      const current = Object.fromEntries(readdirSync(join(c.budgetDirectory,'resources')).map(name => [name,readFileSync(join(c.budgetDirectory,'resources',name),'utf8')]));
      assert.deepEqual(current,c.existingResources,'Managed resources changed or remain; retain ownership');
      c.save('build-slot-handback.json',{builder_lane_absent:true,new_resource_claims_absent:true,allocation:c.limits,
        release_loan_retained:true,checked_at:new Date().toISOString()}); return;
    }
    if (step === 'inspect') {
      await revalidate(); releaseLock = deployment.acquireTargetLock(c.state); await revalidate();
      runtime = makeRuntime(); await runtime.inspectTarget(); assert.equal(runtime.checkMigrationRetry(),false);
      before = await inventory();
      for (const [service,container] of Object.entries(c.preparedInventory)) {
        assert.equal(before[service].Id,container.Id,'Concurrent service change: '+service); assert.equal(before[service].Image,container.Image);
      }
      for (const service of c.servicePlan.preservedServices)
        assert.deepEqual(c.newConfig.services[service],c.oldConfig.services[service],'Dependency configuration changed: '+service);
      const expectedBackup = structuredClone(c.oldConfig.services['postgres-backup']);
      for (const [name,value] of [['command',c.servicePlan.backup.command],['stop_signal',c.servicePlan.backup.stopSignal]]) {
        if (value.present) expectedBackup[name] = value.value; else delete expectedBackup[name];
      }
      assert.deepEqual(c.newConfig.services['postgres-backup'],expectedBackup,'Generated backup differs from validated service plan');
      assert.equal(before.postgres.HostConfig.Init,true);
      hashes = new Map((await runtime.compose(['--profile','maintenance','config','--hash','*'])).stdout.trim().split('\n').map(line => line.trim().split(/\s+/)));
      for (const service of [...c.servicePlan.updatedServices,...c.servicePlan.preservedServices])
        assert.ok(hashes.get(service),'Missing generated configuration hash: '+service);
      for (const service of c.servicePlan.preservedServices)
        assert.equal(hashes.get(service),before[service].Config.Labels['com.docker.compose.config-hash'],'Unchanged service drift: '+service);
      originallyRunning = await runtime.runningServices();
      const required = [...runtimeModule.applicationServices,'postgres','redis','postgres-backup'].filter(name => c.oldConfig.services[name]);
      assert.ok(required.every(name => originallyRunning.includes(name)),
        'Required original services are not running: '+required.filter(name => !originallyRunning.includes(name)).join(', '));
      assert.equal(before['postgres-backup'].Image,c.backupInputs.image,'Original backup image differs');
      c.schema = await runtime.checkSchema(); c.save('schema-before.json',c.schema);
      assert.ok(c.schema.applied_versions.at(-1)<=c.schema.expected_version,'Candidate cannot downgrade schema');
      const space = statfsSync(c.evidence), free = Number(space.bavail)*Number(space.bsize);
      assert.ok(free>c.minimumFreeBytes,'Insufficient conservative backup/restore capacity');
      c.save('capacity-before-pause.json',{available_bytes:free,required_reserve_bytes:c.minimumFreeBytes});
      if (c.helperCpus !== undefined) c.save('helper-cpu-only.json',{services:Object.fromEntries(['migration','postgres-backup'].map(s => [s,{cpus:c.helperCpus}]))});
      return {pendingMigrations:c.schema.pending_versions.length>0,migrationRecovery:false};
    }
    if (step === 'quiet') return budget.waitForStudyQuiet({signal:AbortSignal.any([AbortSignal.timeout(60000),c.signal].filter(Boolean))});
    if (step === 'stop') {
      await revalidate(false);
      assert.deepEqual((await runtime.runningServices()).sort(),[...originallyRunning].sort(),'Running services changed before shutdown');
      stopped = true; return runtime.stopApplications();
    }
    if (step === 'checkpoint-before') { c.baseline = await checkpoint('preservation-before.json'); assert.deepEqual(c.baseline.ledger,c.schema.applied_versions); return; }
    if (step === 'backup') { c.backup = await runtime.backup(); c.save('backup-receipt.json',c.backup); return c.backup; }
    if (step === 'migrate') { migrationStarted = true; return runtime.migrate(); }
    if (step === 'verify-migration') {
      const schema = await runtime.checkSchema(); c.save('schema-after.json',schema);
      assert.equal(schema.applied_versions.at(-1),c.schema.expected_version); assert.deepEqual(schema.pending_versions,[]);
      runtime.resolveMigrationGuard(); const after = await checkpoint('preservation-after-migration.json');
      assert.deepEqual(after.ledger,[...c.baseline.ledger,...c.schema.pending_versions]);
      const oldState = {...c.baseline}, newState = {...after}; delete oldState.ledger; delete newState.ledger;
      assert.deepEqual(newState,oldState,'Stopped-writer protected state changed'); return;
    }
    if (step === 'activate') {
      activationStarted = true;
      const changed = c.servicePlan.updatedServices.filter(name => originallyRunning.includes(name));
      await runtime.compose(['up','-d','--no-build','--no-deps','--pull','never','--wait','--wait-timeout','180',...changed],{echo:true});
      const unchanged = originallyRunning.filter(name => !changed.includes(name));
      if (unchanged.length) await runtime.compose(['up','-d','--no-build','--no-deps','--no-recreate',...unchanged],{echo:true});
      c.appliedServices = {updatedServices:changed,preservedServices:unchanged,
        backupConfigurationRevision:c.servicePlan.backup.changed ? c.revision : null};
      return;
    }
    if (step === 'verify-ready') return checkReadyIdentity();
    if (step === 'commit') {
      await revalidate(false);
      const guard = existsSync(join(c.state,'migration-guard.json')) ? read(join(c.state,'migration-guard.json')) : null;
      if (migrationStarted) assert.ok(guard?.state==='verified' && guard.verified_schema===c.schema.expected_version,'Original history guard unresolved');
      const record = {...c.previous,revision:c.revision,schema:c.schema.expected_version,images:c.images,
        composeFiles:[join(c.evidence,'compose-release.json')],imageOverride:join(c.evidence,'images.json'),
        configFingerprint:runtimeModule.configurationFingerprint(c.newConfig),verified_at:new Date().toISOString(),
        backup:c.backup ?? c.previous.backup,evidence:{...c.ciEvidence,
          component_revisions:{...c.previous.evidence?.component_revisions,api:c.revision,web:c.revision,backend:c.revision},
          release_services:c.appliedServices,
          backup_configuration:c.servicePlan.backup.changed ? {revision:c.revision,image:c.backupInputs.image,
            configuration_hash:hashes.get('postgres-backup')} : c.previous.evidence?.backup_configuration ?? null,
          artifact_verification:join(c.evidence,'artifact-verification.json'),release_workflow:join(c.evidence,'release-operation.json')}};
      deployment.atomicJson(join(c.state,'deployment.json'),record);
      const operation = {id:migrationStarted ? guard.attempt_id : c.runId,revision:c.revision,phase:'ready',
        preservation_verified:migrationStarted,migration_required:migrationStarted,completed_at:new Date().toISOString(),schema_before:c.schema.applied_versions.at(-1),schema_after:c.schema.expected_version};
      c.save('release-operation.json',operation); deployment.atomicJson(join(c.state,'operation.json'),operation);
      assert.deepEqual(read(join(c.state,'deployment.json')),record,'Committed deployment readback differs');
      stopped = false; c.committed = record; return record;
    }
    if (step === 'verify-previous') {
      releaseLock = deployment.acquireTargetLock(c.state); await revalidate();
      runtime = makeRuntime(true); await runtime.inspectTarget(); assert.equal(runtime.checkMigrationRetry(),false);
      await runtime.ensureImages(); const schema = await runtime.checkSchema();
      assert.equal(schema.applied_versions.at(-1),c.previous.schema,'Previous receipt schema differs');
      const identity = await runtime.inspectRunningRevision();
      assert.equal(identity.status,'consistent'); assert.ok(identity.receipt.startsWith('matches'),'Previous receipt does not match running services');
      await runtime.verifyReady();
      await runtime.commitDeployment();
      assert.deepEqual(read(join(c.state,'deployment.json')),c.previous,'Fallback must preserve the previous authoritative deployment receipt');
      c.save('release-operation.json',{id:c.runId,revision:c.previous.revision,requested_revision:c.revision,
        phase:'ready_previous_version',completed_at:new Date().toISOString()});
      return {ready:{verified:true,receiptVerified:true,revision:c.previous.revision,url:c.target.webUrl},receipt:c.previous};
    }
    if (step === 'installed-checks') {
      const head = await runtime.compose(['--profile','maintenance','run','--rm','--no-deps','-T','migration','-c',queueProbeCode]);
      c.save('queue-head-readonly.json',JSON.parse(head.stdout));
      await installedChecks(c,run,wrapperDirectory);
      if (c.baseline) {
        const preserved = await runtime.compose(['--profile','maintenance','run','--rm','--no-deps','-T','migration','-c',postPreservationCode,JSON.stringify(c.baseline)]);
        c.save('installed-preservation.json',JSON.parse(preserved.stdout));
      }
      return;
    }
    if (step === 'cleanup') {
      cleanupMode = true;
      const errors = [];
      if (payload.requiredFailed && runtime && stopped) {
        try {
          const guard = existsSync(join(c.state,'migration-guard.json')) ? read(join(c.state,'migration-guard.json')) : null;
          if (!migrationStarted && !activationStarted && guard?.state !== 'pending') {
            const stillOriginal = await inventory();
            for (const service of originallyRunning) {
              assert.equal(stillOriginal[service].Id,before[service].Id,'Original service changed during failure; keep writers stopped');
              assert.equal(stillOriginal[service].Image,before[service].Image);
            }
            const args = ['compose','--project-directory',c.target.root,'--env-file',c.target.envFile,'-p',c.target.project,
              ...c.previous.composeFiles.flatMap(path => ['-f',path]),...(c.previous.imageOverride ? ['-f',c.previous.imageOverride] : []),
              'up','-d','--no-build','--no-deps','--no-recreate',...(originallyRunning ?? [])];
            await docker(args,{echo:true});
          } else await runtime.stopApplications({preservePhase:true});
        } catch (error) { errors.push(String(error)); }
        try { await runtime.recordFailure(payload.failure); } catch (error) { errors.push(String(error)); }
      }
      for (const [name,record] of helpers) {
        try {
          const container = await absentOrOwned(name);
          if (container) await docker(['container','rm','--force',container.Id]);
          assert.equal(await absentOrOwned(name),null,'Helper absence unverified');
          await budget.recordBudgetResource(record.key,false,c.ownerToken,c.budgetDirectory); helpers.delete(name);
        } catch (error) { errors.push(String(error)); }
      }
      const opPath = join(c.state,'operation.json');
      const op = existsSync(opPath) ? read(opPath) : null;
      if (runtime && op?.backup?.restore_created && !op.backup.restore_removed) errors.push('Owned backup restore database remains; retain original operation/backup for its owner');
      if (c.ownerToken) {
        try {
          await budget.validateDevelopmentBudgetOwner({directory:c.budgetDirectory,token:c.ownerToken});
          requireAbsentLane(c.budgetDirectory,'builder-lane');
          requireAbsentLane(c.budgetDirectory,'admission-lane');
          const resources = Object.fromEntries(readdirSync(join(c.budgetDirectory,'resources')).map(name => [name,readFileSync(join(c.budgetDirectory,'resources',name),'utf8')]));
          assert.deepEqual(resources,c.existingResources,'Resource journal changed; retain ownership');
        } catch (error) { errors.push(String(error)); }
      }
      if (allocationRollbackFailure) errors.push('Allocation ownership remains unsettled: '+allocationRollbackFailure);
      try { if (releaseLock && c.committed) attachInstalledEvidence(c,deployment); } catch (error) { errors.push(String(error)); }
      try { releaseLock?.(); releaseLock = undefined; } catch (error) { errors.push(String(error)); }
      const status = {complete:!errors.length,claimAbsent:!c.ownerToken && !loan,allocationRolledBack,
        reason:errors.join('; '),remainingHelpers:Object.fromEntries(helpers),checked_at:new Date().toISOString()};
      try { if (c.save) c.save('resource-cleanup.json',status); }
      catch (error) { status.complete = false; status.reason += '; Cleanup evidence write failed: '+error; }
      return status;
    }
    if (step === 'handback') {
      if (!loan) return {complete:false,owner:'coordinator',reason:'Borrowed lease must be released by its owner'};
      await loan.release(); loan = undefined; return {complete:true};
    }
    if (step === 'closeout') return closeout(c,payload,deployment);
    throw new Error('Unknown release step: '+step);
  };
}
