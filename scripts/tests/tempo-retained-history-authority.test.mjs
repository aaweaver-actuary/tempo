import test from 'node:test';
import assert from 'node:assert/strict';
import {verifyRetainedHistoryBindings, withVerifiedRetainedHistory} from '../tempo-retained-history-authority.mjs';

const hash = n => `sha256:${n.repeat(64)}`;
function fixture() {
  const c = {mode:'retained-history-v1', contractVersion:1, revision:'a'.repeat(40), schema:hash('b'),
    sourceSha:'9'.repeat(40), sourceSchema:hash('8'), sourceAuthority:'old', targetAuthority:'new', transferId:'transfer', manifestDigest:hash('c'), generation:'gen',
    lockIdentity:'old-lock', workspace:'ws', graphSourceReference:'graph', graphReceipt:'graph-receipt',
    images:{learning:hash('d'), postgres:hash('e'), 'study-web':hash('f')}, volumes:{data:{name:'data',external:true}}, database:{name:'study',volume:'data'},
    ingress:{origin:'http://127.0.0.1:1234',hosts:['127.0.0.1']}, composeFiles:['/tmp/compose.yml']};
  const registration = {version:2, cutover:c, volumes:c.volumes, composeFiles:c.composeFiles,
    webUrl:c.ingress.origin, daemonId:'daemon', context:'ctx', project:'project', root:'/tmp/root', envFile:'/tmp/env', postgresVolumeKey:'data', postgresMajor:18, postgresImage:c.images.postgres, ports:[{service:'study-web',host:'127.0.0.1',port:'1234',target:80}]};
  const observation = {scope:'observed', evidenceId:'observed', registration, manifestDigest:c.manifestDigest,
    currentSourceWatermark:'watermark', manifest:{source_inventory:[{shape:{authority:'old',store:'cards',schema_evidence:'schema',primary_key:[['id','text']]},expected_count:0,expected_digest:hash('2'),completeness_evidence:'capture'}], target_shapes:[{authority:'new',store:'cards',schema_evidence:'schema',primary_key:[['id','text']]}], family_inventory:['content','cards_history','position','issued','queue','games','tactics','browser'], family_evidence:['content','cards_history','position','issued','queue','games','tactics','browser'].map(f=>[f,'evidence']), study_timezone:'UTC', scheduler_identity:'scheduler', facts:[], version:1, source_sha:c.sourceSha, source_schema:c.sourceSchema, scope:'complete', capture:'final_fenced', transfer_id:c.transferId, source_authority:c.sourceAuthority,
      target_authority:c.targetAuthority, target_schema:c.schema, source_watermark:'watermark', fence_id:'fence', unresolved:[]},
    verification:{version:1,status:'verified',blockers:[],verifier_evidence:'verification',transfer_id:c.transferId,
      manifest_digest:c.manifestDigest,target_generation:c.generation},
    fence:{version:1,transfer_id:c.transferId,fence_id:'fence',source_authority:c.sourceAuthority,target_authority:c.targetAuthority,
      source_watermark:'watermark',lock_identity:c.lockIdentity,denied_writer_inventory_digest:hash('1'),
      stale_session_denial:'session',credential_denial:'credential',restart_denial:'restart',evidence:'native-owner'},
    activation:{version:1,transfer_id:c.transferId,manifest_digest:c.manifestDigest,fence_id:'fence',target_generation:c.generation,
      target_authority:c.targetAuthority,activation_id:'activation',target_acceptance:'accepted'},
    artifacts:{revision:c.revision,schema:c.schema,images:c.images,volumes:c.volumes,ingress:c.ingress,workspace:c.workspace,
      composeFiles:c.composeFiles,graphSourceReference:c.graphSourceReference,graphReceipt:c.graphReceipt,database:c.database,
      ports:registration.ports,postgresImage:registration.postgresImage,postgresMajor:registration.postgresMajor,postgresVolumeKey:registration.postgresVolumeKey,
      root:registration.root,envFile:registration.envFile,daemonId:registration.daemonId,context:registration.context,project:registration.project}};
  return {registration:structuredClone(registration), observation:structuredClone(observation)};
}
test('retained v1 binds observed publication and distinct final receipts', () => {
  const {registration,observation}=fixture();
  assert.equal(verifyRetainedHistoryBindings(registration,observation),true);
  for (const mutate of [
    o => o.currentSourceWatermark='stale', o => o.manifest.source_watermark='stale', o => o.verification.manifest_digest=hash('2'),
    o => o.activation.target_generation='old', o => o.fence.lock_identity='other',
    o => o.artifacts.images.learning=hash('2'), o => o.manifest.scope='representative',
    o => o.activation.target_acceptance='uncertain', o => o.fence.stale_session_denial='',
    o => o.scope='disposable', o => o.verification.blockers.push('hold')]) {
    const changed=structuredClone(observation); mutate(changed);
    assert.throws(() => verifyRetainedHistoryBindings(registration,changed), /retained_history_/);
  }
  const legacy=structuredClone(registration); legacy.cutover.mode='graph-preserving-fresh-start';
  assert.throws(() => verifyRetainedHistoryBindings(legacy,observation), /registration_incomplete/);
});
test('reobserves under original lock and rejects changed source before effect', async () => {
  const {registration,observation}=fixture(); let reads=0, effects=0;
  const observe=async()=>{ const value=structuredClone(observation); if (++reads===2) value.manifest.source_watermark='new'; return value; };
  await assert.rejects(withVerifiedRetainedHistory({registration,observe,withLock:async (_lock,cb)=>cb()},async()=>{effects++;}), /retained_history_/);
  assert.equal(effects,0);
});
test('rejects missing or repeated lock callback', async () => {
  const {registration,observation}=fixture(); let effects=0;
  const observe=async()=>structuredClone(observation);
  await assert.rejects(withVerifiedRetainedHistory({registration,observe,withLock:async()=>0},async()=>{effects++;}), /lock_callback_invalid/);
  await assert.rejects(withVerifiedRetainedHistory({registration,observe,withLock:async (_lock,cb)=>{await cb(); return cb();}},async()=>{effects++;}), /lock_callback_invalid/);
  assert.equal(effects,1);
});

test('rejects malformed target identity and incomplete manifest', () => {
  const {registration,observation}=fixture();
  for (const mutate of [
    r => {r.composeFiles=['relative']; r.cutover.composeFiles=['relative'];},
    r => {r.volumes.data.external=false; r.cutover.volumes.data.external=false;},
    r => {r.cutover.database.volume='wrong';}, r => {r.postgresImage='postgres:latest';},
    r => {r.ports[0].host='different';}, r => {r.cutover.ingress.hosts=[];},
    r => {r.root='relative';}]) {
    const bad=structuredClone(registration); mutate(bad); const o=structuredClone(observation); o.registration=structuredClone(bad);
    assert.throws(()=>verifyRetainedHistoryBindings(bad,o), /registration_incomplete/);
  }
  for (const key of ['source_inventory','target_shapes','family_inventory','family_evidence','facts','study_timezone','scheduler_identity']) {
    const bad=structuredClone(observation); delete bad.manifest[key];
    assert.throws(()=>verifyRetainedHistoryBindings(registration,bad), /manifest_unverified/);
  }
});
test('caller mutation during observation or lock blocks the action', async () => {
  for (const when of ['observe','lock']) {
    const {registration,observation}=fixture(); let effects=0;
    const observe=async()=>{if (when==='observe') registration.project='changed'; return structuredClone(observation);};
    const withLock=async (_lock,cb)=>{if (when==='lock') registration.project='changed'; return cb();};
    await assert.rejects(withVerifiedRetainedHistory({registration,observe,withLock},async()=>{effects++;}), /registration_changed/);
    assert.equal(effects,0);
  }
});
test('action receives immutable independent observation', async () => {
  const {registration,observation}=fixture();
  await withVerifiedRetainedHistory({registration,observe:async()=>observation,withLock:async (_lock,cb)=>cb()},async current=>{
    assert.ok(Object.isFrozen(current.manifest)); assert.throws(()=>{current.manifest.source_watermark='changed';},TypeError);
  });
  assert.equal(observation.manifest.source_watermark,'watermark');
});

test('blocked or incomplete captured facts cannot reach an action', async () => {
  for (const change of [
    f => {f.disposition='BLOCK'; f.blocker='hold';},
    f => {f.disposition='ARCHIVE'; f.archive_readback='archive';},
    f => {f.target_key=null;},
    f => {f.disposition='TRANSLATE'; f.translation_proof=null;},
    (_f,m) => {m.source_inventory[0].expected_count=2;}
  ]) {
    const {registration,observation}=fixture(); const m=observation.manifest;
    const fact={family:'cards_history', source:{authority:'old',store:'cards',primary_key:[{name:'id',codec:'text',value:'1'}]},
      codec:'codec',original_base64:'',original_digest:hash('3'),references:[],required:true,disposition:'EXACT',target_owner:'new',
      target_key:{authority:'new',store:'cards',primary_key:[{name:'id',codec:'text',value:'1'}]},translation_proof:null,archive_readback:null,blocker:null};
    m.facts=[fact]; m.source_inventory[0].expected_count=1; change(fact,m); let effects=0;
    await assert.rejects(withVerifiedRetainedHistory({registration,observe:async()=>observation,withLock:async (_lock,cb)=>cb()},async()=>{effects++;}),/manifest_unverified/);
    assert.equal(effects,0);
  }
});
test('explicit HTTPS IPv6 origin is accepted when ingress and port match', () => {
  const {registration,observation}=fixture();
  registration.webUrl='https://[::1]:8443'; registration.cutover.ingress={origin:registration.webUrl,hosts:['::1']};
  registration.ports=[{service:'study-web',host:'::1',port:'8443',target:80}];
  observation.registration=structuredClone(registration); observation.artifacts.ingress=structuredClone(registration.cutover.ingress);
  observation.artifacts.ports=structuredClone(registration.ports);
  assert.equal(verifyRetainedHistoryBindings(registration,observation),true);
  registration.cutover.ingress.hosts=['other']; observation.registration=structuredClone(registration);
  assert.throws(()=>verifyRetainedHistoryBindings(registration,observation),/registration_incomplete/);
});


test('retained ingress rejects published non-web, non-loopback, mismatched and duplicate bindings', () => {
  const cases = [
    ['Postgres', r => r.ports.push({service:'postgres',host:'127.0.0.1',port:'5432',target:5432})],
    ['Learning', r => r.ports.push({service:'learning',host:'127.0.0.1',port:'8000',target:8000})],
    ['wildcard', r => {r.webUrl='http://0.0.0.0:1234'; r.cutover.ingress={origin:r.webUrl,hosts:['0.0.0.0']}; r.ports[0].host='0.0.0.0';}],
    ['non-loopback', r => {r.webUrl='http://192.0.2.1:1234'; r.cutover.ingress={origin:r.webUrl,hosts:['192.0.2.1']}; r.ports[0].host='192.0.2.1';}],
    ['other ingress host', r => r.cutover.ingress.hosts.push('0.0.0.0')],
    ['mismatched port', r => {r.ports[0].port='1235';}],
    ['mismatched origin', r => {r.webUrl='http://127.0.0.1:1235';}],
    ['extra web mapping', r => r.ports.push({service:'study-web',host:'127.0.0.1',port:'1235',target:80})],
    ['duplicate mapping', r => r.ports.push({...r.ports[0]})],
    ['UDP', r => {r.ports[0].protocol='udp';}],
    ['wrong target', r => {r.ports[0].target=81;}],
    ['malformed origin', r => {r.webUrl='not a URL'; r.cutover.ingress.origin=r.webUrl;}],
  ];
  for (const [name, mutate] of cases) {
    const {registration,observation}=fixture(); mutate(registration);
    observation.registration=structuredClone(registration);
    observation.artifacts.ingress=structuredClone(registration.cutover.ingress);
    observation.artifacts.ports=structuredClone(registration.ports);
    assert.throws(() => verifyRetainedHistoryBindings(registration,observation), /registration_incomplete/, name);
  }
});
