import {createServer}from'node:http';import{readFile}from'node:fs/promises';
const[path,port,credentialPath]=process.argv.slice(2),credential=(await readFile(credentialPath,'utf8')).trim();
const server=createServer(async(request,response)=>{if(request.headers.authorization!=='Bearer '+credential){response.writeHead(401);response.end('{}');return;}let state={active:true,available:false};try{const snapshot=JSON.parse(await readFile(path,'utf8'));if(snapshot.expires>Date.now())state=snapshot;}catch{}response.setHeader('Content-Type','application/json');response.end(JSON.stringify(state));});
await new Promise(resolve=>server.listen(Number(port),'127.0.0.1',resolve));
for(const signal of['SIGTERM','SIGINT'])process.once(signal,()=>{server.closeAllConnections();server.close(()=>process.exit(0));});
