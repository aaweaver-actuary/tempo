import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync, readdirSync, writeFileSync} from 'node:fs';
import {join, resolve, relative} from 'node:path';
import {isDeepStrictEqual} from 'node:util';

const applicationUpdates = ['foreground-worker','background-worker','background-scheduler','api','web'];
const preservedDependencies = ['postgres','redis','defense-engine','maia-worker'];
const digest = value => createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key,canonical(value[key])])) : value;
const field = (service,name) => Object.hasOwn(service,name)
  ? {present:true,value:structuredClone(service[name])} : {present:false};

// Inputs are resolved Compose services; the caller establishes image equivalence.
export function planReleaseServices(oldConfig, selectedBackupService) {
  const old = oldConfig.services?.['postgres-backup'];
  assert.ok(old && selectedBackupService,'Required postgres-backup definition is missing');
  for (const name of new Set([...Object.keys(old),...Object.keys(selectedBackupService)])) {
    if (['command','stop_signal'].includes(name)) continue;
    if (!isDeepStrictEqual(field(old,name),field(selectedBackupService,name))) {
      // Values may contain credentials. Keep the observed difference private-safe.
      const summary = service => Object.hasOwn(service,name) ? 'sha256:'+digest(JSON.stringify(service[name])) : 'absent';
      throw new Error(`Unsupported postgres-backup field ${name}: previous ${summary(old)}, selected ${summary(selectedBackupService)}`);
    }
  }
  const changed = ['command','stop_signal'].some(name => !isDeepStrictEqual(field(old,name),field(selectedBackupService,name)));
  return {backup:{changed,command:field(selectedBackupService,'command'),stopSignal:field(selectedBackupService,'stop_signal')},
    updatedServices:[...applicationUpdates.filter(name => oldConfig.services[name]),...(changed ? ['postgres-backup'] : [])],
    preservedServices:[...preservedDependencies,...(changed ? [] : ['postgres-backup'])].filter(name => oldConfig.services[name])};
}

export async function prepareReleaseServices(context, run) {
  const names = ['docker-compose.yml','docker-compose.postgres-maintenance.yml'];
  const supported = names.map(name => resolve(context.target.root,name));
  assert.deepEqual(context.target.composeFiles?.map(path => resolve(path)),supported,
    'Unsupported registered Compose layout: '+JSON.stringify(context.target.composeFiles));
  const files = names.map(name => join(context.source,name));
  const inputs = [...files,...(context.target.envFile ? [context.target.envFile] : [])];
  const inputHashes = Object.fromEntries(inputs.map(path => [path,digest(readFileSync(path))]));
  const args = ['--context',context.target.context,'compose','--project-directory',context.source,
    ...(context.target.envFile ? ['--env-file',context.target.envFile] : []),'-p',context.target.project,
    ...files.flatMap(path => ['-f',path]),'--profile','maintenance','config','--format','json'];
  const selected = JSON.parse((await run('docker',args,{cwd:context.source})).stdout).services?.['postgres-backup'];
  assert.ok(selected,'Selected source has no postgres-backup service');
  const immutable = context.oldConfig.services?.['postgres-backup']?.image;
  assert.match(immutable ?? '',/^sha256:/,'Saved backup image must be immutable');
  assert.equal(context.previous.images['postgres-backup'],immutable,'Saved backup image receipt differs');
  const [image] = JSON.parse((await run('docker',['--context',context.target.context,'image','inspect',selected.image],{cwd:context.source})).stdout);
  assert.equal(image?.Id,immutable,'Selected backup image differs from the preserved immutable image');
  const normalized = {...selected,image:immutable};
  return {servicePlan:planReleaseServices(context.oldConfig,normalized),backupInputs:{revision:context.revision,inputHashes,
    definitionHash:digest(JSON.stringify(canonical(normalized))),image:immutable,imageStopSignal:image.Config?.StopSignal || 'SIGTERM'}};
}

// Retained prepare.py source-to-image mapping; no runtime source overlay.
const mapping = {'backend/app':'/app/app', 'database/tempo_legacy_database':'/app/tempo_legacy_database'};
for (const path of ['packages/scheduling','packages/chess-core','services/learning/application','services/learning/runtime',
  'services/learning/remediation','contracts/content-publication','contracts/game-evidence','database/tempo_learning_database',
  'database/tempo_remediation_database','packages/game-review-contracts','contracts/analysis-jobs','packages/evidence-math',
  'packages/game-ingestion','packages/repertoire-comparison','packages/game-classification']) mapping[path] = '/' + path;

export async function prepareBuild(context, run) {
  const manifest = JSON.parse(context.buildManifestText);
  assert.deepEqual(Object.keys(manifest.services).sort(), ['api','web'], 'Only retained api/web build targets are supported');
  for (const service of Object.values(manifest.services)) {
    assert.equal(resolve(service.build.context), context.source, 'Build context differs from selected checkout');
    assert.equal(service.build.labels['org.opencontainers.image.revision'], context.revision);
    assert.ok(service.image, 'Missing immutable build tag');
  }
  const hashes = {};
  const walk = (directory, visit) => {
    for (const entry of readdirSync(directory, {withFileTypes:true})) {
      const path = join(directory, entry.name);
      if (entry.isDirectory() && entry.name !== '__pycache__') walk(path, visit);
      else if (entry.isFile() && /\.(py|toml|json)$/.test(entry.name)) visit(path);
    }
  };
  for (const [source, destination] of Object.entries(mapping)) {
    const directory = join(context.source, source);
    walk(directory, path => { hashes[destination + '/' + relative(directory,path)] = createHash('sha256').update(readFileSync(path)).digest('hex'); });
  }
  const bake = (await run('docker', ['--context',context.target.context,'compose','-f',context.buildManifestPath,
    'build','--print','api','web'], {cwd:context.source})).stdout;
  assert.ok(Object.keys(JSON.parse(bake).target ?? {}).length, 'No selected Compose build targets');
  return {buildManifest:manifest, sourceHashes:hashes, bake};
}

export async function buildCandidate(context, budget, run) {
  // The existing shared interface owns BuildKit choice, --load, kernel limits,
  // quiet admission, builder lane and verified builder cleanup.
  await budget.managedComposeBuild(() => context.bake,
    args => run('docker', ['--context',context.target.context,...args], {cwd:context.source, echo:true}),
    {limits:context.limits, token:context.ownerToken, directory:context.budgetDirectory,
      docker:async args => (await run('docker',['--context',context.target.context,...args])).stdout,
      quiet:() => budget.waitForStudyQuiet({signal:context.signal})});
  const images = {};
  for (const [service, configured] of Object.entries(context.buildManifest.services)) {
    const image = JSON.parse((await run('docker',['--context',context.target.context,'image','inspect',configured.image])).stdout)[0];
    assert.equal(image.Config.Labels['org.opencontainers.image.revision'], context.revision, 'Image revision mismatch');
    assert.match(image.Id, /^sha256:/);
    images[service] = {tag:configured.image, id:image.Id, created:image.Created};
  }
  context.save('image-receipt.json', {revision:context.revision, images, completed_at:new Date().toISOString(),
    builder_cpu_budget:context.limits.cpus, builder_memory_mib:context.limits.memoryMiB});
  return images;
}

export async function verifyArtifacts(context, runHelper) {
  const receipt = context.load('image-receipt.json');
  assert.equal(receipt.revision, context.revision);
  const api = receipt.images.api.id, web = receipt.images.web.id;
  const paths = JSON.stringify(Object.keys(context.sourceHashes));
  const code = 'import json,hashlib;from pathlib import Path;paths=json.loads(' + JSON.stringify(paths)
    + ');print(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}))';
  const observed = JSON.parse((await runHelper('artifact-api', api, ['--entrypoint','python'], ['-c',code], 256)).stdout);
  assert.deepEqual(observed, context.sourceHashes, 'Packaged source differs from selected checkout');
  const assets = (await runHelper('artifact-web', web, ['--entrypoint','sh'],
    ['-ec','find /usr/share/nginx/html -type f -exec sha256sum {} +'], 64)).stdout;
  const index = assets.split('\n').filter(line => line.endsWith(' /usr/share/nginx/html/index.html'));
  assert.equal(index.length, 1);
  writeFileSync(join(context.evidence,'web-assets.sha256'), assets, {mode:0o600});
  context.save('artifact-verification.json', {revision:context.revision, api, web,
    verified_runtime_source_files:Object.keys(observed).length, source_hashes:observed,
    web_index_sha256:index[0].split(/\s+/)[0], web_asset_count:assets.trim().split('\n').length, source_overlay:false});
  const configured = structuredClone(context.oldConfig);
  const images = {...context.previous.images};
  for (const service of ['api','foreground-worker','background-worker','background-scheduler','migration']) {
    if (configured.services[service]) { configured.services[service].image = api; images[service] = api; }
  }
  configured.services.web.image = web; images.web = web;
  const plan = context.servicePlan;
  assert.ok(plan,'Missing validated release service plan');
  if (plan.backup.changed) {
    for (const [name,desired] of [['command',plan.backup.command],['stop_signal',plan.backup.stopSignal]]) {
      if (desired.present) configured.services['postgres-backup'][name] = structuredClone(desired.value);
      else delete configured.services['postgres-backup'][name];
    }
  }
  for (const mount of configured.services.migration.volumes)
    if (mount.target === '/source') mount.source = context.source;
  context.save('compose-release.json', configured);
  context.save('images.json', {services:Object.fromEntries(Object.entries(images).map(([name,image]) => [name,{image}]))});
  context.newConfig = configured; context.images = images;
}
