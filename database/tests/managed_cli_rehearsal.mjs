// Owned managed CLI rehearsal. This driver is never part of the maintenance image.
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, readFileSync, realpathSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { atomicJson, targetKey } from '../../scripts/tempo-deployment.mjs';
import { studyDurabilityPgn } from './postgres-test-fixture.mjs';
import { Chess } from 'chess.js';

export async function runOwnedManagedCliRehearsal({ root, project, target, config, context, daemonId, images,
  revision, environment, stateRoot, stateDirectory, registration, directory, compose, docker, readHistory, expected, readLatestMigration, manifest }) {
  const harness = environment.W08_MANAGED_HARNESS;
  if (!harness || !environment.W08_REAL_DOCKER?.startsWith('/')) throw new Error('Owned managed harness paths are missing');
  if (!environment.W08_OWNED_CLONE || !environment.W08_OWNED_BARE || realpathSync(root)!==realpathSync(environment.W08_OWNED_CLONE) ||
      !realpathSync(root).startsWith('/private/tmp/') || !realpathSync(environment.W08_OWNED_BARE).startsWith('/private/tmp/'))
    throw new Error('Owned managed clone identity differs');
  const git=(...args)=>{const result=spawnSync('git',args,{cwd:root,encoding:'utf8',timeout:30000});if(result.status!==0)throw new Error('Owned git inspection failed');return result.stdout.trim()};
  if(git('rev-parse','HEAD')!==revision || git('symbolic-ref','--short','HEAD')!=='main' ||
      git('status','--porcelain','--untracked-files=all')!=='' || realpathSync(git('remote','get-url','origin'))!==realpathSync(environment.W08_OWNED_BARE))
    throw new Error('Owned managed clone provenance differs');
  const selectionDirectory = join(directory, 'approved'); mkdirSync(selectionDirectory, { mode: 0o700 }); chmodSync(selectionDirectory, 0o700);
  const selectionPath = join(selectionDirectory, 'selection.json');
  assert.deepEqual(manifest.expected.groups, { no_activity: 1, recognition_only: 1, reviewed: 1 });
  writeFileSync(selectionPath, JSON.stringify(manifest), { mode: 0o600 });
  let dbId = (await compose(['ps','-q','postgres'])).stdout.trim();
  assert.match(dbId, /^[0-9a-f]{64}$/);
  const db = JSON.parse((await docker(['inspect',dbId])).stdout)[0];
  const initialDbId=dbId;
  const clusterFacts=async id=>JSON.parse((await docker(['exec','--user','postgres',id,'psql','-X','-A','-t','-U','postgres','-d','postgres','-c',
    "SELECT json_build_object('system',(select system_identifier::text from pg_control_system()),'target',(select oid::bigint from pg_database where datname='tempo'),'control',(select oid::bigint from pg_database where datname='postgres'))"])).stdout);
  const originalCluster=await clusterFacts(dbId);
  const endpoints = Object.values(db.NetworkSettings.Networks); assert.equal(endpoints.length,1);
  const sha = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  for(const name of ['migration','api','web']) {
    const image=JSON.parse((await docker(['image','inspect',images[name]])).stdout)[0];
    assert.equal(image.Id,images[name]);assert.equal(image.Config?.Labels?.['org.opencontainers.image.revision'],revision);
  }
  const policyDir = join(directory,'harness-policy'); mkdirSync(policyDir,{mode:0o700}); chmodSync(policyDir,0o700);
  const policyPath = join(policyDir,'policy.json');
  const policy = { context, daemon:daemonId, db_id:dbId, db_image:db.Image, image:images.migration, revision,
    project, target_key:targetKey(target), state_dir:stateDirectory, passfile:config.secrets.admin_pgpass.file,
    hook_path:join(harness,'sitecustomize.py'), hook_sha256:sha(join(harness,'sitecustomize.py')),
    owner_sha256:sha(join(root,'database/tooling/managed_isolation.py')), network_id:endpoints[0].NetworkID,
    data_volume:target.volumes[target.postgresVolumeKey].name,cluster_facts:originalCluster };
  writeFileSync(policyPath,JSON.stringify(policy),{mode:0o600});
  const sourceText=readFileSync(join(root,'scripts/tempo-managed-isolation.mjs'),'utf8');
  const gate="export function requireManagedIsolationGate() {\n  throw new Error('Nonempty Defense cleanup is unavailable without a verified writer and DDL fence.');\n}";
  assert.equal(sourceText.split(gate).length,2);
  const evidence={revision,project,daemonId,context,images,dbId,networkId:policy.network_id,
    ownerSourceSha256:sha(join(root,'scripts/tempo-managed-isolation.mjs')),
    ownerTransformedSha256:createHash('sha256').update(sourceText.replace(gate,'export function requireManagedIsolationGate() {}')).digest('hex'),
    pythonOwnerSha256:policy.owner_sha256,loaderSha256:sha(join(harness,'loader.mjs')),registerSha256:sha(join(harness,'register.mjs')),
    hookSha256:policy.hook_sha256,proxySha256:sha(join(harness,'docker')),policySha256:sha(policyPath),
    pythonOverride:'task-only sitecustomize enables the closed owner gate only in the verified pinned child',
    clone:realpathSync(root),bareOrigin:realpathSync(environment.W08_OWNED_BARE),ci:'disabled'};
  atomicJson(join(directory,'managed-rehearsal-preflight.json'),evidence);
  const record=episode=>writeFileSync(join(directory,'managed-rehearsal-evidence.json'),JSON.stringify({...evidence,episode}),{mode:0o600});
  const commonEnv = { ...environment, PATH:`${harness}:${process.env.PATH}`, NODE_OPTIONS:`--import=${join(harness,'register.mjs')}`,
    W08_REAL_DOCKER:environment.W08_REAL_DOCKER, W08_POLICY:policyPath, W08_OWNER_PATH:join(root,'scripts/tempo-managed-isolation.mjs'),
    W08_EXPECTED_OWNER_SHA:sha(join(root,'scripts/tempo-managed-isolation.mjs')), W08_HOOK_DIR:harness,
    TEMPO_CLI_STATE_DIR:stateRoot };
  const cli = (fault, ...args) => {
    const result = spawnSync(process.execPath,['scripts/tempo-cli.mjs',...args,'--config',registration],
      {cwd:root,env:{...commonEnv,W08_FAULT:fault},encoding:'utf8',timeout:600000,maxBuffer:20*1024*1024});
    if (result.error) throw result.error;
    return result;
  };
  const fired = fault => {
    const path=join(policyDir,`fault-${fault}.json`);
    assert.deepEqual(JSON.parse(readFileSync(path,'utf8')),{fault,project,db_id:dbId});
    assert.equal(statSync(path).mode & 0o777,0o600);
  };
  const bindCurrentBeforeIsolation = async candidate => {
    assert.match(candidate,/^[0-9a-f]{64}$/);
    const current=JSON.parse((await docker(['inspect',candidate])).stdout)[0];
    assert.equal(current.Image,db.Image);assert.equal(current.Config.Labels['com.docker.compose.project'],project);
    assert(current.Mounts.some(item=>item.Name===policy.data_volume&&item.Destination==='/var/lib/postgresql'&&item.RW));
    assert.deepEqual(await clusterFacts(candidate),originalCluster);
    const attached=Object.values(current.NetworkSettings.Networks);assert.equal(attached.length,1);assert.equal(attached[0].NetworkID,policy.network_id);
    dbId=candidate;policy.db_id=dbId;atomicJson(policyPath,policy);
    atomicJson(join(directory,'managed-rehearsal-pre-isolation-rebind.json'),{initialDbId,dbId,clusterFacts:originalCluster,
      volume:policy.data_volume,networkId:policy.network_id,policySha256:sha(policyPath)});
  };
  const closed = spawnSync(process.execPath,['scripts/tempo-cli.mjs','start','--no-ci','--no-open','--defense-selection',selectionPath,'--config',registration],
    {cwd:root,env:{...commonEnv,NODE_OPTIONS:'',W08_FAULT:''},encoding:'utf8',timeout:30000});
  assert.notEqual(closed.status,0);assert.match(closed.stderr,/Nonempty Defense cleanup is unavailable/);
  const readGuard = () => JSON.parse(readFileSync(join(stateDirectory,'migration-guard.json')));
  const assertFailed = result => assert.notEqual(result.status,0,'owned fault must interrupt real CLI');
  const isolatedSchema = async () => {
    const inspect = async () => JSON.parse((await docker(['inspect',dbId])).stdout)[0];
    const current=await inspect();assert.equal(current.Id,dbId);assert.equal(Object.keys(current.NetworkSettings.Networks).length,0);
    const sql = async (database, statement) => (await docker(['exec','--user','postgres',dbId,'psql','-X','-A','-t','-v','ON_ERROR_STOP=1',
      '-U','postgres','-d',database,'-c',statement])).stdout.trim();
    assert.equal(await sql('postgres',"SELECT count(*) FROM pg_stat_activity WHERE datname='tempo'"),'0');
    assert.equal(await sql('postgres',"SELECT count(*) FROM pg_prepared_xacts WHERE database='tempo'"),'0');
    const wasOpen=await sql('postgres',"SELECT datallowconn FROM pg_database WHERE datname='tempo'");
    assert.equal(wasOpen,'f','interrupted managed cut must leave admission closed');
    if(wasOpen==='f') await sql('postgres','ALTER DATABASE tempo ALLOW_CONNECTIONS true');
    try { return Number(await sql('tempo','SELECT max(version) FROM tempo_schema_migrations')); }
    finally { if(wasOpen==='f')await sql('postgres','ALTER DATABASE tempo ALLOW_CONNECTIONS false');
      assert.equal(Object.keys((await inspect()).NetworkSettings.Networks).length,0); }
  };
  if (environment.W08_COPY_ONLY === '1') {
    const failure=cli('selection-copy','start','--no-ci','--no-open','--defense-selection',selectionPath);
    assertFailed(failure);fired('selection-copy');assert.match(failure.stderr,/W08_INJECT_SELECTION_COPY/);
    const guard=readGuard();assert.equal(guard.starting_schema,59);assert.equal(guard.backup.verified,true);
    assert.equal(guard.isolation,undefined);await bindCurrentBeforeIsolation((await compose(['ps','-q','postgres'])).stdout.trim());
    assert.equal(guard.managed_selection_started,true);assert.equal(guard.defense_binding,undefined);
    assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),59);
    assertFailed(cli('','start','--retry','--no-ci','--no-open'));
    const after=readGuard();assert.equal(after.attempt_id,guard.attempt_id);assert.deepEqual(after.backup,guard.backup);
    assert.equal(after.defense_binding,undefined);assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),59);
    record('selection-copy-failure');return { copyOnly:true };
  }
  if (environment.W08_CAPTURE_ONLY === '1') {
    assertFailed(cli('capture-after-file','start','--no-ci','--no-open','--defense-selection',selectionPath));
    const captureMarker=JSON.parse(readFileSync(join(policyDir,'fault-capture-after-file.json'),'utf8'));
    assert.equal(readGuard().isolation,undefined);
    await bindCurrentBeforeIsolation(captureMarker.db_id);fired('capture-after-file');
    const guard=readGuard();assert.equal(guard.starting_schema,59);assert.equal(guard.backup.verified,true);
    assert.equal(guard.cut62_capture_started,true);assert.equal(guard.cut62_identity,undefined);
    const path=join(stateDirectory,guard.cut62_witness.slice(0,-5),guard.cut62_witness);
    const bytes=readFileSync(path),inode=statSync(path).ino;assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),61);
    assertFailed(cli('','start','--retry','--no-ci','--no-open'));
    assert.equal(statSync(path).ino,inode);assert.deepEqual(readFileSync(path),bytes);
    const after=readGuard();assert.equal(after.attempt_id,guard.attempt_id);assert.deepEqual(after.backup,guard.backup);
    assert.equal(after.cut62_identity,undefined);assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),61);
    record('capture-file-before-id');return { captureOnly:true };
  }
  const first = cli('schema60','start','--no-ci','--no-open','--defense-selection',selectionPath); assertFailed(first);
  const faultMarker=JSON.parse(readFileSync(join(policyDir,'fault-schema60.json'),'utf8'));
  assert.equal(faultMarker.fault,'schema60');assert.equal(faultMarker.project,project);assert.match(faultMarker.db_id,/^[0-9a-f]{64}$/);
  await bindCurrentBeforeIsolation(faultMarker.db_id);
  fired('schema60');
  const original = readGuard(); assert.equal(original.starting_schema,59); assert.equal(original.backup.verified,true);
  assert.equal(original.isolation,undefined);
  assert.equal(original.managed_pre_isolation.identity.containerId,dbId);
  assert.equal(original.managed_pre_isolation.system_identifier,originalCluster.system);
  assert.equal(original.managed_pre_isolation.target_oid,originalCluster.target);
  assert.equal(original.managed_pre_isolation.control_oid,originalCluster.control);
  assert.equal(original.managed_selection_started,true);assert.match(original.defense_binding.backup_sha256,/^[0-9a-f]{64}$/);
  const preWitness=join(stateDirectory,original.pre62_witness.slice(0,-5),original.pre62_witness);
  const preBytes=readFileSync(preWitness), preInode=statSync(preWitness).ino;
  assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),60);
  renameSync(preWitness,preWitness+'.original');
  try { assertFailed(cli('','start','--retry','--no-ci','--no-open'));assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),60); }
  finally { renameSync(preWitness+'.original',preWitness); }
  renameSync(preWitness,preWitness+'.original');writeFileSync(preWitness,'tampered',{mode:0o600});
  try { assertFailed(cli('','start','--retry','--no-ci','--no-open'));assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),60); }
  finally { unlinkSync(preWitness);renameSync(preWitness+'.original',preWitness); }
  assert.equal(statSync(preWitness).ino,preInode);assert.deepEqual(readFileSync(preWitness),preBytes);
  const second60=cli('schema60-repeat','start','--retry','--no-ci','--no-open');assertFailed(second60);fired('schema60-repeat');
  const repeated=readGuard();assert.deepEqual(repeated.managed_pre_isolation,original.managed_pre_isolation);
  assert.equal(repeated.attempt_id,original.attempt_id);assert.deepEqual(repeated.backup,original.backup);
  assert.equal((await compose(['ps','-q','postgres'])).stdout.trim(),dbId);
  assert.deepEqual(await clusterFacts(dbId),originalCluster);assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),60);
  assert.equal(statSync(preWitness).ino,preInode);assert.deepEqual(readFileSync(preWitness),preBytes);
  const selectionCopy = join(stateDirectory,original.cut62_witness.slice(0,-5),'defense-selection.json');
  const bindingPath = join(stateDirectory,original.cut62_witness.slice(0,-5),'binding.json');
  const selectionBytes = readFileSync(selectionCopy), bindingBytes = readFileSync(bindingPath), selectionInode = statSync(selectionCopy).ino;
  assert.deepEqual(selectionBytes,readFileSync(selectionPath));
  assert.equal(JSON.parse(bindingBytes).backup_filename,original.backup.filename);
  const capturedResult = cli('before-detach','start','--retry','--no-ci','--no-open'); assertFailed(capturedResult);fired('before-detach');
  const captured = readGuard(); assert.equal(captured.attempt_id,original.attempt_id); assert.deepEqual(captured.backup,original.backup);
  assert.equal(captured.isolation.phase,'intent');
  assert.deepEqual(captured.defense_binding,original.defense_binding);assert.deepEqual(readFileSync(preWitness),preBytes);assert.equal(statSync(preWitness).ino,preInode);
  assert.deepEqual(readFileSync(selectionCopy),selectionBytes); assert.deepEqual(readFileSync(bindingPath),bindingBytes);
  assert.equal(statSync(selectionCopy).ino,selectionInode); assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),61);
  const witnessDir=join(stateDirectory,captured.cut62_witness.slice(0,-5)), witness=join(witnessDir,captured.cut62_witness);
  const witnessBytes=readFileSync(witness), witnessInode=statSync(witness).ino;
  const alternateScript=`import json,os,psycopg\nfrom database.tooling.verify_postgres_cli_state import load_cut62_history_selection,load_cut62_attempt_binding,save_cut62_history_v2\ns,d=load_cut62_history_selection('/cut62/defense-selection.json')\nb=load_cut62_attempt_binding('/cut62/binding.json',s,d)\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL']) as db:\n with db.transaction():\n  db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')\n  print(json.dumps({'witness_id':save_cut62_history_v2('/cut62/alternate.json',db,s,b,d,b)}))`;
  const alternate=JSON.parse((await compose(['--profile','maintenance','run','--rm','--no-deps','-T','--volume',`${witnessDir}:/cut62`,
    'migration','-c',alternateScript])).stdout); assert.notEqual(alternate.witness_id,captured.cut62_identity.witness_id);
  const alternatePath=join(witnessDir,'alternate.json'), alternateBytes=readFileSync(alternatePath);
  const substitute = async callback => { renameSync(witness,witness+'.original'); writeFileSync(witness,alternateBytes,{mode:0o600});
    try { await callback(); } finally { unlinkSync(witness); renameSync(witness+'.original',witness); } };
  // Fault after physical detach but before its marker; preserve true admission
  // across an exact-container cold stop and let the normal retry reconcile it.
  const admission = async () => (await docker(['exec','--user','postgres',dbId,'psql','-X','-A','-t','-U','postgres','-d','postgres','-c',
    "SELECT datallowconn FROM pg_database WHERE datname='tempo'"])).stdout.trim();
  assert.equal(await admission(),'t');
  await docker(['network','disconnect',policy.network_id,dbId]); await docker(['stop','--time','10',dbId]);
  let coldTopology=JSON.parse((await docker(['inspect',dbId])).stdout)[0];
  assert.equal(coldTopology.State.Running,false);assert.equal(Object.keys(coldTopology.NetworkSettings.Networks).length,0);
  await substitute(async()=>{ assertFailed(cli('','start','--retry','--no-ci','--no-open')); assert.equal(await isolatedSchema(),61); });
  const afterCommit=cli('after-commit','start','--retry','--no-ci','--no-open'); assertFailed(afterCommit);fired('after-commit');
  const pending=readGuard(); assert.equal(pending.attempt_id,original.attempt_id); assert.deepEqual(pending.backup,original.backup);
  assert.equal(pending.isolation.phase,'detached');
  assert.deepEqual(pending.defense_binding,original.defense_binding);assert.deepEqual(readFileSync(preWitness),preBytes);assert.equal(statSync(preWitness).ino,preInode);
  assert.equal(await isolatedSchema(),62);
  const receipt=join(witnessDir,'verified-receipt.json'); assert(!existsSync(receipt));
  // Let retry reconcile the previous helper journal, then interrupt before it
  // reopens closed admission. The next cold retry must use the same DB ID.
  assertFailed(cli('before-admission-open','start','--retry','--no-ci','--no-open'));fired('before-admission-open');
  assert(!existsSync(join(witnessDir,'pinned-helper.json')));
  assert.equal(await admission(),'f');
  await docker(['stop','--time','10',dbId]);
  coldTopology=JSON.parse((await docker(['inspect',dbId])).stdout)[0];
  assert.equal(coldTopology.State.Running,false);assert.equal(Object.keys(coldTopology.NetworkSettings.Networks).length,0);
  await substitute(async()=>{ assertFailed(cli('','start','--retry','--no-ci','--no-open')); assert(!existsSync(receipt)); assert.equal(await isolatedSchema(),62); });
  assert.equal(statSync(witness).ino,witnessInode); assert.deepEqual(readFileSync(witness),witnessBytes);
  const afterReceipt=cli('after-receipt','start','--retry','--no-ci','--no-open'); assertFailed(afterReceipt);fired('after-receipt');assert(existsSync(receipt));
  assert.equal(await isolatedSchema(),62);
  const originalReceiptBytes=readFileSync(receipt), originalReceiptInode=statSync(receipt).ino;
  const originalReceipt=JSON.parse(originalReceiptBytes), alternateReceipt={...originalReceipt,attempt_id:'00000000-0000-0000-0000-000000000000'};
  const completionPath=join(witnessDir,'verified-completion.json'); assert(!existsSync(completionPath));
  const receiptGuard=readGuard(), payload={version:1,target:receiptGuard.target,attempt_id:alternateReceipt.attempt_id,
    identity:receiptGuard.isolation.identity,target_oid:receiptGuard.isolation.target_oid,system_identifier:receiptGuard.isolation.system_identifier,
    control_oid:receiptGuard.isolation.control_oid,backup:receiptGuard.defense_binding,pre62_witness_id:receiptGuard.pre62_witness_id,
    cut62_witness_id:receiptGuard.cut62_identity.witness_id,receipt:alternateReceipt};
  const sorted=value=>Array.isArray(value)?value.map(sorted):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,sorted(value[key])])):value;
  const signature=createHmac('sha256',Buffer.from(JSON.parse(witnessBytes).secret,'hex')).update('tempo-disposable-completion-v1\0').update(JSON.stringify(sorted(payload))).digest('hex');
  const independent=spawnSync(environment.W08_REAL_DOCKER,['--context',context,'run','--rm','-i','--network','none',
    '--label',`tempo.disposable.owner=${project}`,images.migration,'-c',
    "import hashlib,hmac,json,sys;x=json.load(sys.stdin);s=hmac.new(bytes.fromhex(x['secret']),b'tempo-disposable-completion-v1\\0'+json.dumps(x['payload'],sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest();sys.exit(0 if hmac.compare_digest(s,x['signature']) else 1)"],
    {input:JSON.stringify({payload,signature,secret:JSON.parse(witnessBytes).secret}),encoding:'utf8',timeout:30000});
  assert.equal(independent.status,0,'alternate completion must have an independently valid HMAC');
  renameSync(receipt,receipt+'.original');writeFileSync(receipt,JSON.stringify(alternateReceipt),{mode:0o600});
  writeFileSync(completionPath,JSON.stringify({payload,signature}),{mode:0o600});
  try { assertFailed(cli('','start','--retry','--no-ci','--no-open')); assert.equal(await isolatedSchema(),62); }
  finally { unlinkSync(receipt);renameSync(receipt+'.original',receipt);unlinkSync(completionPath); }
  assert.equal(statSync(receipt).ino,originalReceiptInode);assert.deepEqual(readFileSync(receipt),originalReceiptBytes);
  assertFailed(cli('before-reattach','start','--retry','--no-ci','--no-open'));fired('before-reattach');
  let phase=readGuard();assert.equal(phase.isolation.phase,'verified');assert(existsSync(completionPath));
  let topology=JSON.parse((await docker(['inspect',dbId])).stdout)[0];assert.equal(Object.keys(topology.NetworkSettings.Networks).length,0);
  assertFailed(cli('after-reattach','start','--retry','--no-ci','--no-open'));fired('after-reattach');
  phase=readGuard();assert.equal(phase.isolation.phase,'verified');
  topology=JSON.parse((await docker(['inspect',dbId])).stdout)[0];assert.equal(Object.keys(topology.NetworkSettings.Networks).length,1);
  assertFailed(cli('before-app-start','start','--retry','--no-ci','--no-open'));fired('before-app-start');
  assert.equal(readGuard().state,'verified');
  const completed=cli('','start','--no-ci','--no-open'); assert.equal(completed.status,0,completed.stderr);
  const final=readGuard(); assert.equal(final.state,'verified'); assert.equal(final.attempt_id,original.attempt_id); assert.deepEqual(final.backup,original.backup);
  assert.deepEqual(final.defense_binding,original.defense_binding);assert.equal(final.cut62_identity.witness_id,captured.cut62_identity.witness_id);
  assert.equal(statSync(preWitness).ino,preInode);assert.deepEqual(readFileSync(preWitness),preBytes);
  assert.equal(statSync(selectionCopy).ino,selectionInode);assert.deepEqual(readFileSync(selectionCopy),selectionBytes);assert.deepEqual(readFileSync(bindingPath),bindingBytes);
  assert.equal(statSync(witness).ino,witnessInode); assert.deepEqual(readFileSync(witness),witnessBytes);
  assert.deepEqual(await readHistory(),expected);
  const completion=join(witnessDir,'verified-completion.json'), completionBytes=readFileSync(completion), completionInode=statSync(completion).ino;
  const request=async(path,options={})=>{
    const response=await fetch(`${target.webUrl}/api/${path}`,options);
    if(response.status!==202){if(!response.ok)throw new Error(`Owned Study request failed: ${response.status}`);return response.json()}
    const pending=await response.json();
    for(let i=0;i<120;i++){await new Promise(resolve=>setTimeout(resolve,250));const receipt=await fetch(`${target.webUrl}/api/operations/${encodeURIComponent(pending.operation_id)}`).then(r=>r.json());
      if(receipt.state==='complete')return receipt.response;if(receipt.state==='failed')throw new Error('Owned Study operation failed');}
    throw new Error('Owned Study operation did not settle');
  };
  const settings=await request('settings');
  await request('settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...settings,new_cards_per_day:100,study_new_per_day:100})});
  const form=new FormData();form.set('file',new Blob([studyDurabilityPgn],{type:'application/x-chess-pgn'}),'owned.pgn');
  form.set('trained_color','white');form.set('initial_depth','6');
  const imported=await request('imports/pgn',{method:'POST',body:form});let card;
  for(let i=0;i<120;i++){try{card=(await request('queue/today')).cards.find(item=>item.repertoire_id===imported.repertoire_id);if(card)break}catch{}await new Promise(resolve=>setTimeout(resolve,500));}
  assert(card?.queue_entry_id&&card.moves?.length&&new Chess(card.start_fen).move(card.moves[0]));
  const post=(path,payload,id)=>request(path,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':id},body:JSON.stringify(payload)});
  await post(`cards/${card.id}/teaching`,{revision:card.revision,ply:0},'w08-managed-teaching');
  await post(`cards/${card.id}/review`,{outcome:'correct',queue_entry_id:card.queue_entry_id,expected_revision:card.revision,attempt_id:'w08-managed-attempt'},'w08-managed-review');
  let next;for(let i=0;i<120;i++){try{next=(await request('queue/today')).cards.find(item=>item.queue_entry_id!==card.queue_entry_id&&item.moves?.length);if(next)break}catch{}await new Promise(resolve=>setTimeout(resolve,500));}
  assert(next&&new Chess(next.start_fen).move(next.moves[0]));
  const reviewReceipt=await request('operations/w08-managed-review');assert.equal(reviewReceipt.state,'complete');
  const warm=cli('','restart','--no-ci','--no-open');assert.equal(warm.status,0,warm.stderr);
  const stopped=cli('','stop');assert.equal(stopped.status,0,stopped.stderr);
  const cold=cli('','start','--no-ci','--no-open');assert.equal(cold.status,0,cold.stderr);
  assert.deepEqual(await request('operations/w08-managed-review'),reviewReceipt);
  const playable=(await request('queue/today')).cards.find(item=>item.moves?.length&&item.start_fen);
  assert(playable&&new Chess(playable.start_fen).move(playable.moves[0]));
  assert.equal(statSync(completion).ino,completionInode);assert.deepEqual(readFileSync(completion),completionBytes);
  record('full-managed-lifecycle');
  return { cli, final, original, completion, completionBytes, completionInode, witness, witnessBytes, witnessInode };
}
