/** The regular browser entry owns its explicit public fixture build. */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { navigationFixture } from './navigation-fixture.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
if(!process.argv[2])throw new Error('Explicit native Intelligence fixture required');
const environment={...Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('TEMPO_')&&!key.startsWith('PG')&&!key.startsWith('PYTHON'))),...navigationFixture};
for(const command of [['npm','run','build'],[process.execPath,'tests/browser.mjs',...process.argv.slice(2)]]){
  const result=spawnSync(command[0],command.slice(1),{cwd:root,env:environment,encoding:'utf8',maxBuffer:16*1024*1024});
  process.stdout.write(result.stdout??'');process.stderr.write(result.stderr??'');
  if(result.error||result.status!==0){process.exitCode=1;throw new Error('Owning browser build or execution failed');}
}
