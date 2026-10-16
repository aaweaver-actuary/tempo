import { IntelligenceClient } from './client.js';
import { commandSchema } from './contracts.js';

const legacyKey = 'tempo-analysis-command-v1';
const prefix = 'tempo-analysis-command-v2:';
type Command = {command_id:string;[key:string]:unknown};
type Pending = {version:2;origin:string;workspace:string;path:string;body:string;request_digest:string};

function authorityKey(client:IntelligenceClient) {
  return prefix + JSON.stringify([client.connection.origin,client.connection.workspace]);
}
async function requestDigest(path:string, command:Command) {
  const url=new URL(path,'http://authority.invalid');
  const run=/^\/v1\/analysis\/runs\/([^/]+)\/commands$/.exec(url.pathname);
  const finding=/^\/v1\/analysis\/findings\/([^/]+)\/commands$/.exec(url.pathname);
  let target:Record<string,unknown>;
  if(run && !url.search) target={run_id:decodeURIComponent(run[1])};
  else if(finding && url.searchParams.get('run_id') && Number.isSafeInteger(Number(url.searchParams.get('generation'))) && Number(url.searchParams.get('generation'))>0 && [...url.searchParams.keys()].sort().join(',')==='generation,run_id') {
    target={run_id:url.searchParams.get('run_id'),finding_id:decodeURIComponent(finding[1]),generation:Number(url.searchParams.get('generation'))};
  } else throw new Error('Unsupported saved command target. Pending evidence retained.');
  const payload={...target,schema_version:1,budget:250,...command};
  const canonical=JSON.stringify(payload,Object.keys(payload).sort());
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical));
  return 'sha256:'+Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');
}

/** One unresolved mutation per exact authority, persisted before any transmission. */
export class PendingCommands {
  private dispatching=new Set<string>();
  constructor(private readonly storage:Storage) {}

  async pending(client:IntelligenceClient):Promise<Pending|null> {
    const storageKey=authorityKey(client);
    const legacyRaw=this.storage.getItem(legacyKey);
    if(legacyRaw) {
      const legacy=JSON.parse(legacyRaw) as {origin:string;workspace:string;path:string;command:Command};
      if(legacy.origin===client.connection.origin && legacy.workspace===client.connection.workspace) {
        const migrated:Pending={version:2,origin:legacy.origin,workspace:legacy.workspace,path:legacy.path,body:JSON.stringify(legacy.command),request_digest:await requestDigest(legacy.path,legacy.command)};
        const existing=this.storage.getItem(storageKey);
        if(existing && existing!==JSON.stringify(migrated))throw new Error('Conflicting saved commands require receipt recovery. Both records retained.');
        this.storage.setItem(storageKey,JSON.stringify(migrated));
        this.storage.removeItem(legacyKey);
      }
    }
    const raw=this.storage.getItem(storageKey);
    if(!raw)return null;
    const saved=JSON.parse(raw) as Pending;
    if(saved.version!==2 || saved.origin!==client.connection.origin || saved.workspace!==client.connection.workspace || typeof saved.body!=='string')throw new Error('Invalid saved command authority. Pending evidence retained.');
    const command=JSON.parse(saved.body) as Command;
    if(typeof command.command_id!=='string' || saved.request_digest!==await requestDigest(saved.path,command))throw new Error('Saved command identity mismatch. Pending evidence retained.');
    return saved;
  }

  async assertAvailable(client:IntelligenceClient) {
    if(this.dispatching.has(authorityKey(client)) || await this.pending(client))throw new Error('Recover the pending command before another change.');
  }

  private confirm(client:IntelligenceClient,saved:Pending,receipt:ReturnType<typeof commandSchema.parse>) {
    const command=JSON.parse(saved.body) as Command;
    if(receipt.command_id!==command.command_id || receipt.request_digest!==saved.request_digest)throw new Error('Command receipt identity mismatch. Pending evidence retained.');
    const storageKey=authorityKey(client);
    if(this.storage.getItem(storageKey)!==JSON.stringify(saved))throw new Error('Saved command changed during recovery.');
    this.storage.removeItem(storageKey);
    return receipt;
  }

  async recover(client:IntelligenceClient,saved:Pending) {
    const command=JSON.parse(saved.body) as Command;
    const receipt=await client.request(`/v1/analysis/commands/${encodeURIComponent(command.command_id)}`,commandSchema);
    return this.confirm(client,saved,receipt);
  }

  async retry(client:IntelligenceClient,saved:Pending) {
    const storageKey=authorityKey(client);
    if(this.dispatching.has(storageKey))throw new Error('The saved command is still being sent.');
    this.dispatching.add(storageKey);
    try {
      const current=await this.pending(client);
      if(JSON.stringify(current)!==JSON.stringify(saved))throw new Error('Saved command changed.');
      const receipt=await client.requestSerialized(saved.path,commandSchema,saved.body);
      return this.confirm(client,saved,receipt);
    } finally {this.dispatching.delete(storageKey);}
  }

  async submit(client:IntelligenceClient,path:string,command:Command) {
    const storageKey=authorityKey(client);
    if(this.dispatching.has(storageKey))throw new Error('Recover the pending command before another change.');
    this.dispatching.add(storageKey);
    try {
      if(await this.pending(client))throw new Error('Recover the pending command before another change.');
      const body=JSON.stringify(command);
      const saved:Pending={version:2,origin:client.connection.origin,workspace:client.connection.workspace,path,body,request_digest:await requestDigest(path,JSON.parse(body) as Command)};
      this.storage.setItem(storageKey,JSON.stringify(saved));
      if(this.storage.getItem(storageKey)!==JSON.stringify(saved))throw new Error('Unable to retain the command. Nothing was sent.');
      const receipt=await client.requestSerialized(path,commandSchema,body);
      return this.confirm(client,saved,receipt);
    } finally {this.dispatching.delete(storageKey);}
  }
}
