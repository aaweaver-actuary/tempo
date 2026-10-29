import { navigationFixture } from './tests/navigation-fixture.mjs';
import { mkdtemp,cp,rm,mkdir,readFile,writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve,join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
const source=resolve(new URL('.',import.meta.url).pathname),destination=resolve(process.argv[2]??source+'/test-results/restricted');
await mkdir(destination,{recursive:true});const temporary=await mkdtemp(join(tmpdir(),'tempo-C-workbench-'));
const report={complete:false,commands:[],source_removed:false};
const environment=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('TEMPO_')&&!key.startsWith('PG')&&!['NODE_OPTIONS','NODE_PATH','PYTHONPATH'].includes(key)));
function run(args,cwd,label,expected=0){const result=spawnSync(args[0],args.slice(1),{cwd,env:environment,encoding:'utf8',maxBuffer:16*1024*1024});report.commands.push({label,args,status:result.status});return writeFile(join(destination,label+'.log'),(result.stdout??'')+(result.stderr??'')).then(()=>{if(result.status!==expected)throw new Error(label+'_failed');return result.stdout;});}
try{
 const buildRoot=join(temporary,'build'),build=join(buildRoot,'apps/workbench-web');await mkdir(build,{recursive:true});
 for(const path of ['src','public','index.html','package.json','package-lock.json','tsconfig.json','vite.config.ts','test.mjs','tests'])await cp(join(source,path),join(build,path),{recursive:true});
 // Preserve the finite source layout for the single canonical outbox storage adapter.
 await cp(resolve(source,'../../database/browser/adapters/workbench'),join(buildRoot,'database/browser/adapters/workbench'),{recursive:true});
 // Install the shared renderer as a real tarball, never a sibling source alias.
 const boardSource=resolve(source,'../../packages/board-ui');
 await run(['npm','run','build','--workspaces=false'],boardSource,'board-build');
 const boardPacked=JSON.parse(await run(['npm','pack','--ignore-scripts','--json','--pack-destination',destination],boardSource,'board-pack'))[0];
 const boardArtifact=join(destination,boardPacked.filename);
 report.boardArtifact={filename:boardPacked.filename,sha256:createHash('sha256').update(await readFile(boardArtifact)).digest('hex')};
 const buildManifest=JSON.parse(await readFile(join(build,'package.json'),'utf8'));
 buildManifest.dependencies['@tempo/board-ui']='file:'+boardArtifact;
 await writeFile(join(build,'package.json'),JSON.stringify(buildManifest,null,2)+'\n');
 await run(['npm','install','--package-lock-only','--workspaces=false','--ignore-scripts'],build,'lock-board-artifact');
 await run(['npm','ci','--workspaces=false','--ignore-scripts'],build,'install');
 await run(['node','tests/public-build-config.mjs'],build,'public-build-settings');
 // Explicit non-secret fixture routing; no live Study service is contacted.
 Object.assign(environment,navigationFixture);
 await run(['npm','run','build'],build,'build');
 const packed=JSON.parse(await run(['npm','pack','--json','--pack-destination',destination],build,'pack'))[0];const artifact=join(destination,packed.filename);report.artifact=packed.filename;report.sha256=createHash('sha256').update(await readFile(artifact)).digest('hex');
 const consumer=join(temporary,'consumer');await mkdir(consumer);await writeFile(join(consumer,'package.json'),JSON.stringify({private:true,type:'module',dependencies:{'@tempo/workbench-web':'file:'+artifact},devDependencies:{'@playwright/test':'1.63.0'}}));
 await run(['npm','install','--workspaces=false','--ignore-scripts'],consumer,'consumer-install');
 await cp(join(build,'tests'),join(consumer,'tests'),{recursive:true});await cp(join(build,'test.mjs'),join(consumer,'test.mjs'));await cp(join(consumer,'node_modules/@tempo/workbench-web/dist'),join(consumer,'dist'),{recursive:true});
 // Tests use only the public installed entry; fixture JSON is actual native C output.

 await rm(buildRoot,{recursive:true});
 await rm(join(consumer,'node_modules/@tempo/board-ui/src'),{recursive:true});
 report.source_removed=true;
 await run(['node','test.mjs'],consumer,'installed-tests');await run(['node','test.mjs','no-such-test-identity'],consumer,'zero-selected',1);
 await run(['node','tests/browser.mjs',join(consumer,'tests/fixtures/producer.json'),join(destination,'browser')],consumer,'native-browser');
 report.complete=true;
}catch(error){report.error=error.message;process.exitCode=1;}finally{await rm(temporary,{recursive:true,force:true});report.temporary_removed=true;await writeFile(join(destination,'restricted.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}
