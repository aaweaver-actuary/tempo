// Owned managed CLI rehearsal. This driver is never part of the maintenance image.
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { chmodSync, closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, realpathSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { atomicJson, redact, targetKey } from '../../scripts/tempo-deployment.mjs';
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
  const testSecrets=realpathSync(environment.TEMPO_PG_TEST_SECRETS), secretDirectory=statSync(testSecrets);
  if(dirname(testSecrets)!==realpathSync(root) || !/^\.tempo-pg-test-secrets-[A-Za-z0-9]+$/.test(basename(testSecrets)) ||
      !secretDirectory.isDirectory() || secretDirectory.uid!==process.getuid() || (secretDirectory.mode&0o777)!==0o700)
    throw new Error('Owned runner secret directory differs');
  const status=git('status','--porcelain','--untracked-files=all','--','.',`:(exclude)${basename(testSecrets)}/**`);
  const candidateRef=environment.W08_CANDIDATE_REF;
  if(!/^refs\/heads\/codex\/[a-z0-9][a-z0-9/-]*$/.test(candidateRef??'') ||
      git('rev-parse','HEAD')!==revision || git('symbolic-ref','--short','HEAD')!=='main' ||
      status!=='' || git('remote','get-url','origin')!=='https://github.com/aaweaver-actuary/tempo.git')
    throw new Error('Owned managed clone provenance differs');
  const remote=git('ls-remote','origin',candidateRef).split(/\s+/);
  if(remote.length!==2||remote[0]!==revision||remote[1]!==candidateRef) throw new Error('Published candidate ref differs');
  git('fetch','origin',candidateRef);
  const candidateTree=git('rev-parse','HEAD^{tree}');
  if(git('rev-parse','FETCH_HEAD')!==revision||git('rev-parse','FETCH_HEAD^{tree}')!==candidateTree)
    throw new Error('Published candidate tree differs');
  // The test runner creates exactly one private secrets directory in the owned
  // clone. Make the real CLI's ordinary Git status ignore that one directory,
  // without masking any tracked or other untracked source changes.
  const excludePath=join(root,'.git','info','exclude'), excludeBefore=readFileSync(excludePath,'utf8');
  const secretPattern=`/${basename(testSecrets)}/`, entries=excludeBefore.split('\n').map(line=>line.trim()).filter(line=>line&&!line.startsWith('#'));
  if(entries.some(line=>line!==secretPattern) || entries.length>1)
    throw new Error('Owned clone Git exclusion file is not pristine');
  const excludeFd=openSync(excludePath,'a');
  try { if(!entries.length)writeFileSync(excludeFd,`\n${secretPattern}\n`);fsyncSync(excludeFd); } finally { closeSync(excludeFd); }
  const excludeDirectory=openSync(dirname(excludePath),'r');try {fsyncSync(excludeDirectory)} finally {closeSync(excludeDirectory)}
  if(git('status','--porcelain','--untracked-files=all')!=='') throw new Error('Owned clone has other untracked source');
  const selectionDirectory = join(directory, 'approved'); mkdirSync(selectionDirectory, { mode: 0o700 }); chmodSync(selectionDirectory, 0o700);
  const selectionPath = join(selectionDirectory, 'selection.json');
  assert.deepEqual(manifest.expected.groups, { no_activity: 1, recognition_only: 1, reviewed: 1 });
  writeFileSync(selectionPath, JSON.stringify(manifest), { mode: 0o600 });
  let dbId = (await compose(['ps','-aq','postgres'])).stdout.trim();
  assert.match(dbId, /^[0-9a-f]{64}$/);
  const db = JSON.parse((await docker(['inspect',dbId])).stdout)[0];
  const baseline=JSON.parse(readFileSync(join(directory,'managed-baseline.json'),'utf8'));
  assert.equal(baseline.postgresId,dbId);assert.equal(baseline.postgresImage,db.Image);assert.equal(db.State.Running,false);
  const initialDbId=dbId;
  const clusterFacts=async id=>JSON.parse((await docker(['exec','--user','postgres',id,'psql','-X','-A','-t','-U','postgres','-d','postgres','-c',
    "SELECT json_build_object('system',(select system_identifier::text from pg_control_system()),'target',(select oid::bigint from pg_database where datname='tempo'),'control',(select oid::bigint from pg_database where datname='postgres'))"])).stdout);
  const originalCluster=baseline.facts;
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
    owner_sha256:sha(join(root,'database/tooling/managed_isolation.py')), network_id:baseline.networkId,
    data_volume:target.volumes[target.postgresVolumeKey].name,cluster_facts:originalCluster,history_digest:baseline.historyDigest };
  assert.equal(policy.data_volume,baseline.dataVolume);
  writeFileSync(policyPath,JSON.stringify(policy),{mode:0o600});
  const sourceText=readFileSync(join(root,'scripts/tempo-managed-isolation.mjs'),'utf8');
  const gate="export function requireManagedIsolationGate() {\n  throw new Error('Nonempty Defense cleanup is unavailable without a verified writer and DDL fence.');\n}";
  assert.equal(sourceText.split(gate).length,2);
  const selectorPath=join(root,'scripts/tempo-deployment.mjs'), selectorText=readFileSync(selectorPath,'utf8');
  assert.equal(selectorText.split('"refs/heads/main"').length,3);assert.equal(selectorText.split('"fetch", "origin", "main"').length,2);
  const transformedSelector=selectorText.replaceAll('"refs/heads/main"',JSON.stringify(candidateRef))
    .replace('"fetch", "origin", "main"',`"fetch", "origin", ${JSON.stringify(candidateRef)}`);
  const migrationOverlay=target.composeFiles.find(file=>file===join(directory,'migration-build.json'));
  assert(migrationOverlay);assert.deepEqual(JSON.parse(readFileSync(migrationOverlay,'utf8')),
    {services:{migration:{build:{context:root,dockerfile:'Dockerfile.postgres-maintenance'}}}});
  const evidence={revision,candidateTree,project,daemonId,context,images,dbId,networkId:policy.network_id,candidateRef,
    migrationOverlaySha256:sha(migrationOverlay),migrationDockerfileSha256:sha(join(root,'Dockerfile.postgres-maintenance')),
    ownerSourceSha256:sha(join(root,'scripts/tempo-managed-isolation.mjs')),
    ownerTransformedSha256:createHash('sha256').update(sourceText.replace(gate,'export function requireManagedIsolationGate() {}')).digest('hex'),
    selectorSourceSha256:sha(selectorPath),selectorTransformedSha256:createHash('sha256').update(transformedSelector).digest('hex'),
    pythonOwnerSha256:policy.owner_sha256,loaderSha256:sha(join(harness,'loader.mjs')),registerSha256:sha(join(harness,'register.mjs')),
    hookSha256:policy.hook_sha256,proxySha256:sha(join(harness,'docker')),policySha256:sha(policyPath),
    pythonOverride:'task-only sitecustomize enables the closed owner gate only in the verified pinned child',
    clone:realpathSync(root),bootstrapBare:realpathSync(environment.W08_OWNED_BARE),origin:git('remote','get-url','origin'),runnerSecrets:testSecrets,
    gitExcludeSha256:sha(excludePath),
    runnerSecretsUid:secretDirectory.uid,runnerSecretsMode:'0700',ci:'disabled'};
  atomicJson(join(directory,'managed-rehearsal-preflight.json'),evidence);
  const record=episode=>{
    const latest=git('ls-remote','origin',candidateRef).split(/\s+/);
    if(latest.length!==2||latest[0]!==revision||latest[1]!==candidateRef||git('rev-parse','HEAD')!==revision||
       git('rev-parse','HEAD^{tree}')!==candidateTree||git('status','--porcelain','--untracked-files=all')!=='' ||
       sha(join(harness,'loader.mjs'))!==evidence.loaderSha256||sha(join(harness,'register.mjs'))!==evidence.registerSha256||
       sha(join(harness,'sitecustomize.py'))!==evidence.hookSha256||sha(join(harness,'docker'))!==evidence.proxySha256)
      throw new Error('Owned candidate source changed during rehearsal');
    atomicJson(join(directory,'managed-rehearsal-evidence.json'),{...evidence,episode});
  };
  const commonEnv = { ...environment, PATH:`${harness}:${process.env.PATH}`, NODE_OPTIONS:`--import=${join(harness,'register.mjs')}`,
    W08_REAL_DOCKER:environment.W08_REAL_DOCKER, W08_POLICY:policyPath, W08_OWNER_PATH:join(root,'scripts/tempo-managed-isolation.mjs'),
    W08_EXPECTED_OWNER_SHA:sha(join(root,'scripts/tempo-managed-isolation.mjs')), W08_HOOK_DIR:harness,
    W08_SELECTOR_PATH:selectorPath,W08_EXPECTED_SELECTOR_SHA:sha(selectorPath),W08_CANDIDATE_REF:candidateRef,
    TEMPO_CLI_STATE_DIR:stateRoot };
  const secrets=['admin_password','reader_password','writer_password','admin_pgpass','reader_pgpass','writer_pgpass']
    .map(name=>readFileSync(join(environment.TEMPO_PG_TEST_SECRETS,name),'utf8').trim());
  let invocation=0;
  const cli = (fault, ...args) => {
    const result = spawnSync(process.execPath,['scripts/tempo-cli.mjs',...args,'--config',registration],
      {cwd:root,env:{...commonEnv,W08_FAULT:fault},encoding:'utf8',timeout:600000,maxBuffer:20*1024*1024});
    if (result.error) throw result.error;
    const sequence=String(++invocation).padStart(2,'0');
    atomicJson(join(directory,`managed-cli-${sequence}.json`),{
      fault,status:result.status,signal:result.signal,stdout:redact(result.stdout??'',secrets),stderr:redact(result.stderr??'',secrets)});
    const dockerCall=(...args)=>spawnSync(environment.W08_REAL_DOCKER,['--context',context,...args],{encoding:'utf8',timeout:15000});
    const listed=dockerCall('ps','-aq','--filter',`label=com.docker.compose.project=${project}`,'--filter','label=com.docker.compose.service=postgres');
    if(listed.status!==0)throw new Error('Owned PostgreSQL telemetry inventory failed');
    const ids=listed.stdout.trim().split(/\s+/).filter(Boolean);if(ids.length>1)throw new Error('Owned PostgreSQL telemetry identity is ambiguous');
    const telemetry={sequence,fault,cumulative_since_container_start:true,available:false};
    if(ids.length){
      const inspected=dockerCall('inspect',ids[0]);if(inspected.status!==0)throw new Error('Owned PostgreSQL telemetry inspection failed');
      const items=JSON.parse(inspected.stdout),item=items[0];
      if(items.length!==1||item.Id!==ids[0]||item.Image!==db.Image||item.Config?.Labels?.['com.docker.compose.project']!==project||
          item.Config?.Labels?.['com.docker.compose.service']!=='postgres')throw new Error('Owned PostgreSQL telemetry identity differs');
      Object.assign(telemetry,{containerId:item.Id,image:item.Image,running:item.State.Running,oomKilled:item.State.OOMKilled,memoryLimit:item.HostConfig.Memory});
      if(item.State.Running){
        const sample=dockerCall('exec',item.Id,'sh','-c','cat /sys/fs/cgroup/memory.current; cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/memory.events');
        if(sample.status!==0)throw new Error('Owned PostgreSQL cgroup telemetry unavailable');
        const lines=sample.stdout.trim().split('\n');if(!/^\d+$/.test(lines[0]??'')||!/^\d+$/.test(lines[1]??''))throw new Error('Owned PostgreSQL cgroup telemetry malformed');
        Object.assign(telemetry,{available:true,currentBytes:Number(lines[0]),peakBytes:Number(lines[1]),events:lines.slice(2).join('\n')});
      }
    }
    atomicJson(join(directory,`managed-pg-cgroup-${sequence}.json`),telemetry);
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
  atomicJson(join(directory,'managed-cli-shipping-gate.json'),{status:closed.status,signal:closed.signal,
    stdout:redact(closed.stdout??'',secrets),stderr:redact(closed.stderr??'',secrets)});
  assert.notEqual(closed.status,0);assert.match(closed.stderr,/Nonempty Defense cleanup is unavailable/);
  atomicJson(join(directory,'managed-shipping-js-gate.json'),{revision,closed:true,status:closed.status});
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
    const beforeBackup=JSON.parse(readFileSync(join(policyDir,'fault-before-backup.json'),'utf8'));
    assert.equal(guard.isolation,undefined);await bindCurrentBeforeIsolation(beforeBackup.db_id);fired('before-backup');
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
    const beforeBackup=JSON.parse(readFileSync(join(policyDir,'fault-before-backup.json'),'utf8'));
    assert.equal(captureMarker.db_id,beforeBackup.db_id);
    assert.equal(readGuard().isolation,undefined);
    await bindCurrentBeforeIsolation(captureMarker.db_id);fired('before-backup');fired('capture-after-file');
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
  const beforeBackup=JSON.parse(readFileSync(join(policyDir,'fault-before-backup.json'),'utf8'));
  assert.equal(faultMarker.db_id,beforeBackup.db_id);
  assert.equal(faultMarker.fault,'schema60');assert.equal(faultMarker.project,project);assert.match(faultMarker.db_id,/^[0-9a-f]{64}$/);
  await bindCurrentBeforeIsolation(faultMarker.db_id);
  fired('before-backup');fired('schema60');
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
  const preparedBindingPath=join(policyDir,'prepared-image-binding.json');
  const preparedBindingBytes=readFileSync(preparedBindingPath), preparedBinding=JSON.parse(preparedBindingBytes);
  assert.deepEqual(preparedBinding.receipt,captured.managed_image_receipt);
  assert.equal(preparedBinding.attempt_id,captured.attempt_id);assert.equal(preparedBinding.revision,revision);
  assert.equal(preparedBinding.owner_sha256,policy.owner_sha256);
  atomicJson(join(directory,'managed-prepared-image.json'),{bindingSha256:createHash('sha256').update(preparedBindingBytes).digest('hex'),
    bootstrapImage:images.migration,preparedImage:preparedBinding.image,receipt:preparedBinding.receipt,revision});
  assert.deepEqual(captured.defense_binding,original.defense_binding);assert.deepEqual(readFileSync(preWitness),preBytes);assert.equal(statSync(preWitness).ino,preInode);
  assert.deepEqual(readFileSync(selectionCopy),selectionBytes); assert.deepEqual(readFileSync(bindingPath),bindingBytes);
  assert.equal(statSync(selectionCopy).ino,selectionInode); assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),61);
  const witnessDir=join(stateDirectory,captured.cut62_witness.slice(0,-5)), witness=join(witnessDir,captured.cut62_witness);
  const witnessBytes=readFileSync(witness), witnessInode=statSync(witness).ino;
  const planScript=`import json,os,time,psycopg\nfrom psycopg import sql\nfrom database.tooling import verify_postgres_cli_state as g\nselection=json.load(open('/selection.json')); started=time.monotonic()\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL']) as db:\n db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');db.execute('SET LOCAL join_collapse_limit=1');db.execute('SET LOCAL jit=off');db.execute("SET LOCAL statement_timeout='30s'")\n layout=g._cut62_layout(db,g.CUT62_PROTECTED_TABLES|g.CUT62_RETIRED_TABLES|{g.CUT62_LEDGER});keys=layout['threat_training_candidates'][0]\n cohort=\"SELECT c.id card_id,t.id candidate_id,c.repertoire_id FROM jsonb_array_elements(%s::jsonb) approved JOIN public.cards c ON c.id=approved->>0 JOIN public.threat_training_candidates t ON t.id=approved->>1 AND t.card_id=c.id\"\n view=\"SELECT (json_populate_record(t, CASE WHEN EXISTS (SELECT 1 FROM cohort c WHERE c.candidate_id=t.id) THEN '{\\\"card_id\\\":null}'::json ELSE '{}'::json END)).* FROM public.threat_training_candidates t\"\n query=sql.SQL('EXPLAIN (FORMAT JSON) WITH cohort AS ({cohort}), projected AS ({projection}) SELECT row_to_json(t)::text FROM projected t ORDER BY {keys}').format(cohort=sql.SQL(cohort),projection=sql.SQL(view),keys=sql.SQL(',').join(sql.Identifier(k) for k in keys))\n plan=db.execute(query,(json.dumps(selection['keys'],separators=(',',':')),)).fetchone()[0]\n print(json.dumps({'elapsed_seconds':time.monotonic()-started,'plan':plan}))`;
  const plan=JSON.parse((await docker(['run','--rm','--network',policy.network_id,'--memory','256m','--label',`tempo.disposable.owner=${project}`,
    '--volume',`${selectionCopy}:/selection.json:ro`,'--volume',`${config.secrets.admin_pgpass.file}:/run/secrets/admin_pgpass:ro`,
    '--env','PGPASSFILE=/run/secrets/admin_pgpass','--env','TEMPO_POSTGRES_ADMIN_URL=postgresql://postgres@postgres:5432/tempo',
    '--entrypoint','python',preparedBinding.image,'-c',planScript])).stdout);
  assert(Array.isArray(plan.plan)&&Number.isFinite(plan.elapsed_seconds));
  atomicJson(join(directory,'managed-optimized-projection-plan.json'),{revision,image:preparedBinding.image,schema:61,
    joinCollapseLimit:1,jit:false,statementTimeoutMs:30000,...plan});
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
  assert.deepEqual(JSON.parse(readFileSync(join(policyDir,'shipping-python-gate.json'),'utf8')),
    {image:preparedBinding.image,revision,owner_sha256:policy.owner_sha256,closed:true});
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
  const publicationFailure=cli('after-deployment-rename','start','--no-ci','--no-open');
  assertFailed(publicationFailure);fired('after-deployment-rename');
  const published=readFileSync(join(stateDirectory,'deployment.json'));
  const publishedRecord=JSON.parse(published),unsettled=readGuard();
  assert.equal(publishedRecord.revision,revision);assert.equal(publishedRecord.schema,62);
  assert.equal(publishedRecord.images.migration,preparedBinding.image);assert.deepEqual(publishedRecord.backup,original.backup);
  assert.equal(unsettled.state,'verified');assert.equal(unsettled.isolation.phase,'reattached');assert(!unsettled.managed_image_settlement);
  assert.equal(unsettled.cut62_identity.witness_id,captured.cut62_identity.witness_id);
  assert.equal(await admission(),'t');assert.equal(Object.keys(JSON.parse((await docker(['inspect',dbId])).stdout)[0].NetworkSettings.Networks).length,1);
  assert.equal(Number((await readLatestMigration(compose)).stdout.trim()),62);assert(existsSync(receipt));assert(existsSync(completionPath));
  const completed=cli('','start','--no-ci','--no-open'); assert.equal(completed.status,0,completed.stderr);
  assert(!completed.stdout.includes('Tempo: preparing images'),'settlement recovery must reuse the original prepared images');
  const final=readGuard(); assert.equal(final.state,'verified'); assert.equal(final.attempt_id,original.attempt_id); assert.deepEqual(final.backup,original.backup);
  assert.equal(final.managed_image_settlement?.payload?.deployment_sha256,createHash('sha256').update(published).digest('hex'));
  assert.deepEqual(final.defense_binding,original.defense_binding);assert.equal(final.cut62_identity.witness_id,captured.cut62_identity.witness_id);
  assert.equal(statSync(preWitness).ino,preInode);assert.deepEqual(readFileSync(preWitness),preBytes);
  assert.equal(statSync(selectionCopy).ino,selectionInode);assert.deepEqual(readFileSync(selectionCopy),selectionBytes);assert.deepEqual(readFileSync(bindingPath),bindingBytes);
  assert.equal(statSync(witness).ino,witnessInode); assert.deepEqual(readFileSync(witness),witnessBytes);
  assert.deepEqual(await readHistory(),expected);
  assert(final.managed_image_settlement, 'original deployment must have a durable settlement');
  const settlementBytes=readFileSync(join(stateDirectory,'migration-guard.json'));
  await substitute(async()=>{
    assertFailed(cli('','start','--no-ci','--no-open'));
    assert.deepEqual(readFileSync(join(stateDirectory,'migration-guard.json')),settlementBytes);
    assert.equal(await readLatestMigration(compose).then(result=>Number(result.stdout.trim())),62);
  });
  // Prove the replacement is itself a valid owner-authenticated witness and
  // completion pair, then present it against the unchanged original guard.
  const completionPathAfter=join(witnessDir,'verified-completion.json'), settledReceiptBytes=readFileSync(receipt), settledCompletionBytes=readFileSync(completionPathAfter);
  const settledAlternateReceipt={...JSON.parse(settledReceiptBytes),cut62_witness_id:alternate.witness_id};
  const settledAlternatePayload={...JSON.parse(settledCompletionBytes).payload,cut62_witness_id:alternate.witness_id,receipt:settledAlternateReceipt};
  const settledAlternateSignature=createHmac('sha256',Buffer.from(JSON.parse(alternateBytes).secret,'hex'))
    .update('tempo-disposable-completion-v1\0').update(JSON.stringify(sorted(settledAlternatePayload))).digest('hex');
  const settledAlternateCompletion=Buffer.from(JSON.stringify({payload:settledAlternatePayload,signature:settledAlternateSignature}));
  const settledAlternateGuard=structuredClone(final);delete settledAlternateGuard.managed_image_settlement;
  settledAlternateGuard.cut62_identity.witness_id=alternate.witness_id;
  settledAlternateGuard.isolation.completion.sha256=createHash('sha256').update(settledAlternateCompletion).digest('hex');
  await substitute(async()=>{
    renameSync(receipt,receipt+'.original');renameSync(completionPathAfter,completionPathAfter+'.original');
    try {
      writeFileSync(receipt,JSON.stringify(settledAlternateReceipt),{mode:0o600});writeFileSync(completionPathAfter,settledAlternateCompletion,{mode:0o600});
      const ownerScript=`import json,sys\nfrom database.tooling.managed_isolation import authenticate_managed_completion\ng=json.loads(sys.argv[1]); d='/cut62'; p='/pre62/'+g['pre62_witness']\nr=authenticate_managed_completion(g,selection_path=d+'/defense-selection.json',binding_path=d+'/binding.json',witness_path=d+'/'+g['cut62_witness'],receipt_path=d+'/verified-receipt.json',completion_path=d+'/verified-completion.json',pre62_path=p)\nprint(json.dumps(r))`;
      const independentlyAuthenticated=JSON.parse((await docker(['run','--rm','--network','none',
        '--label',`tempo.disposable.owner=${project}`,'--volume',`${witnessDir}:/cut62:ro`,
        '--volume',`${dirname(preWitness)}:/pre62:ro`,'--entrypoint','python',preparedBinding.image,
        '-c',ownerScript,JSON.stringify(settledAlternateGuard)])).stdout);
      assert.equal(independentlyAuthenticated.witness_id,alternate.witness_id);
      assertFailed(cli('','start','--no-ci','--no-open'));
      assert.deepEqual(readFileSync(join(stateDirectory,'migration-guard.json')),settlementBytes);
    } finally {
      unlinkSync(receipt);unlinkSync(completionPathAfter);renameSync(receipt+'.original',receipt);renameSync(completionPathAfter+'.original',completionPathAfter);
    }
  });
  assert.equal(statSync(witness).ino,witnessInode); assert.deepEqual(readFileSync(witness),witnessBytes);
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
  const nextRef=environment.W08_NEXT_CANDIDATE_REF;
  if (!/^refs\/heads\/codex\/[a-z0-9][a-z0-9/-]*$/.test(nextRef??'') || nextRef===candidateRef)
    throw new Error('Owned later candidate ref is required');
  const nextRemote=git('ls-remote','origin',nextRef).split(/\s+/);
  if(nextRemote.length!==2||!/^[0-9a-f]{40}$/.test(nextRemote[0])||nextRemote[0]===revision||nextRemote[1]!==nextRef)
    throw new Error('Owned later candidate ref differs');
  git('fetch','origin',nextRef);
  if(git('rev-parse','FETCH_HEAD')!==nextRemote[0]||git('rev-parse','FETCH_HEAD^{tree}')!==candidateTree)
    throw new Error('Owned later candidate must be an exact-tree descendant');
  if(git('rev-parse','FETCH_HEAD^')!==revision)throw new Error('Owned later candidate must directly follow the original');
  const settledGuardBytes=readFileSync(join(stateDirectory,'migration-guard.json'));
  const nextStop=cli('','stop');assert.equal(nextStop.status,0,nextStop.stderr);
  const stoppedDb=JSON.parse((await docker(['inspect',dbId])).stdout)[0];
  assert.equal(stoppedDb.State.Running,false);assert.equal(stoppedDb.Id,dbId);
  commonEnv.W08_CANDIDATE_REF=nextRef;
  const nextStart=cli('','start','--no-ci','--no-open');assert.equal(nextStart.status,0,nextStart.stderr);
  assert(nextStart.stdout.includes('Tempo: preparing images'),'later revision must perform a real candidate build');
  assert.equal(git('rev-parse','HEAD'),nextRemote[0]);assert.equal(git('rev-parse','HEAD^{tree}'),candidateTree);
  const nextDeployment=JSON.parse(readFileSync(join(stateDirectory,'deployment.json')));
  assert.equal(nextDeployment.revision,nextRemote[0]);assert.equal(nextDeployment.evidence.commit,nextRemote[0]);
  assert.equal(nextDeployment.schema,62);assert.deepEqual(readFileSync(join(stateDirectory,'migration-guard.json')),settledGuardBytes);
  const nextMigration=JSON.parse((await docker(['image','inspect',nextDeployment.images.migration])).stdout)[0];
  assert.equal(nextMigration.Id,nextDeployment.images.migration);assert.equal(nextMigration.Config?.Labels?.['org.opencontainers.image.revision'],nextRemote[0]);
  assert.deepEqual(await request('operations/w08-managed-review'),reviewReceipt);
  const nextPlayable=(await request('queue/today')).cards.find(item=>item.moves?.length&&item.start_fen);
  assert(nextPlayable&&new Chess(nextPlayable.start_fen).move(nextPlayable.moves[0]));
  if(git('ls-remote','origin',nextRef).trim()!==`${nextRemote[0]}\t${nextRef}`)
    throw new Error('Owned later candidate ref changed during the run');
  atomicJson(join(directory,'managed-later-candidate.json'),{initialRevision:revision,nextRevision:nextRemote[0],nextRef,
    tree:candidateTree,migrationImage:nextDeployment.images.migration,guardSha256:createHash('sha256').update(settledGuardBytes).digest('hex'),
    ci:'disabled'});
  return { cli, final, original, completion, completionBytes, completionInode, witness, witnessBytes, witnessInode };
}
