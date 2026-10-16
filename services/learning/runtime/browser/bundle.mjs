// Build the public Study package entries consumed by the real legacy adapter.
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { build } from 'esbuild';

export async function clientBundle(here) {
  const scratch=mkdtempSync(resolve(tmpdir(),'tempo-learning-browser-'));
  const root=resolve(here,'../../../..');
  const compiler=resolve(here,'node_modules/typescript/bin/tsc');
  try {
    for(const name of ['browser-intents','study-session']) {
      const configuration=resolve(scratch,name+'.json');
      writeFileSync(configuration,JSON.stringify({extends:resolve(root,'packages',name,'tsconfig.json'),
        compilerOptions:{outDir:resolve(scratch,name),paths:{'@tempo/browser-intents':[resolve(scratch,'browser-intents/index.d.ts')]}}}));
      execFileSync(process.execPath,[compiler,'-p',configuration],{stdio:'pipe'});
    }
    const bundled=await build({entryPoints:[resolve(here,'client.ts')],bundle:true,write:false,
      format:'iife',globalName:'learningClient',platform:'browser',nodePaths:[resolve(here,'node_modules')],
      alias:{'@tempo/browser-intents':resolve(scratch,'browser-intents/index.js'),
        '@tempo/study-session':resolve(scratch,'study-session/index.js')},
      define:{__TEMPO_API_URL__:'window.location.origin'}});
    return bundled.outputFiles[0].text;
  } finally { rmSync(scratch,{recursive:true,force:true}); }
}
