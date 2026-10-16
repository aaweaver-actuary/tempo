import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildArtifact } from '../build.mjs';
import { checkBoundary } from '../qualify.mjs';
const registration = { artifact:{name:'@tempo/stockfish-worker',version:'1.0.0'}, dependencies:['ajv','chess.js'], publicExports:['StockfishWorker','inspectEngine','stockfishInputDigest'] };
test('owned boundary has declared passive exports and no application imports',()=>{
  assert.equal(checkBoundary(registration).success,true);
  assert.throws(()=>checkBoundary({...registration,publicExports:['missing']}),/exports differ/);
});
test('artifact contains only declared runtime, exact engine assets, schemas and licenses',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'stockfish-artifact-test-'));
  try { const result=await buildArtifact({outputDirectory:directory});
    assert.match(result.sha256,/^[a-f0-9]{64}$/);
    assert.ok(result.files.includes('assets/sf_19_smallnet.wasm'));
    assert.ok(result.files.includes('licenses/THIRD-PARTY.md'));
    assert.equal(result.files.some(name=>/^(?:app|backend|services|tests|node_modules)\//.test(name)),false);
  } finally { await rm(directory,{recursive:true,force:true}); }
});
