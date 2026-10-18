/** Owning build-boundary checks execute the actual Vite config before asset emission. */
import { loadConfigFromFile } from 'vite';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const config=fileURLToPath(new URL('../vite.config.ts',import.meta.url));
const prior=process.env.TEMPO_STUDY_URL;const priorApi=process.env.TEMPO_INTELLIGENCE_ORIGIN;const executedIdentities=[];
try {
  delete process.env.TEMPO_INTELLIGENCE_ORIGIN;
  for(const [identity,value] of [
    ['Study build rejects userinfo','https://user:synthetic-secret@study.example.test/'],
    ['Study build rejects query credentials','https://study.example.test/?token=synthetic-secret'],
    ['Study build rejects unknown query fields','https://study.example.test/?card_id=synthetic-secret'],
    ['Study build rejects unsafe schemes','javascript:alert(1)'],
    ['Study build rejects card-specific paths','https://study.example.test/card/synthetic-secret'],
  ]) {
    process.env.TEMPO_STUDY_URL=value;
    await assert.rejects(()=>loadConfigFromFile({command:'build',mode:'production'},config,undefined,'silent',undefined,'runner'),error=>String(error).includes('TEMPO_STUDY_URL must be a public')&&!String(error).includes(value)&&!String(error).includes('synthetic-secret'));
    executedIdentities.push(identity);
  }
  process.env.TEMPO_STUDY_URL='https://study.example.test/';
  const result=await loadConfigFromFile({command:'build',mode:'production'},config,undefined,'silent',undefined,'runner');
  assert.equal(result.config.define.__TEMPO_STUDY_URL__,JSON.stringify('https://study.example.test/'));executedIdentities.push('Study build emits only a validated public root target');
  for(const [identity,value] of [
    ['API origin build rejects userinfo','https://user:synthetic-secret@api.example.test/'],
    ['API origin build rejects query credentials','https://api.example.test/?token=synthetic-secret'],
    ['API origin build rejects unknown query fields','https://api.example.test/?workspace=synthetic-secret'],
    ['API origin build rejects unsafe schemes','javascript:alert(1)'],
    ['API origin build rejects paths','https://api.example.test/api'],
  ]) {
    process.env.TEMPO_INTELLIGENCE_ORIGIN=value;
    await assert.rejects(()=>loadConfigFromFile({command:'build',mode:'production'},config,undefined,'silent',undefined,'runner'),error=>String(error).includes('TEMPO_INTELLIGENCE_ORIGIN must be a public')&&!String(error).includes(value)&&!String(error).includes('synthetic-secret'));
    executedIdentities.push(identity);
  }
  process.env.TEMPO_INTELLIGENCE_ORIGIN='https://intelligence.example.test/';
  const api=await loadConfigFromFile({command:'build',mode:'production'},config,undefined,'silent',undefined,'runner');
  assert.equal(api.config.define.__TEMPO_INTELLIGENCE_ORIGIN__,JSON.stringify('https://intelligence.example.test'));executedIdentities.push('API origin build emits only a validated origin form default');
  delete process.env.TEMPO_STUDY_URL;delete process.env.TEMPO_INTELLIGENCE_ORIGIN;
  const empty=await loadConfigFromFile({command:'build',mode:'production'},config,undefined,'silent',undefined,'runner');
  assert.equal(empty.config.define.__TEMPO_INTELLIGENCE_ORIGIN__,JSON.stringify(''));
  assert.equal(empty.config.define.__TEMPO_STUDY_URL__,JSON.stringify(''));executedIdentities.push('Public routing defaults are empty with no misleading API port');
  console.log(JSON.stringify({executedIdentities,passed:executedIdentities.length,failed:0,skipped:0,success:true}));
}finally{if(prior===undefined)delete process.env.TEMPO_STUDY_URL;else process.env.TEMPO_STUDY_URL=prior;if(priorApi===undefined)delete process.env.TEMPO_INTELLIGENCE_ORIGIN;else process.env.TEMPO_INTELLIGENCE_ORIGIN=priorApi;}
