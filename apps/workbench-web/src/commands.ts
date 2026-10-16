import { IntelligenceClient } from './client.js';
import { commandSchema, runRequestSchema, runStatusSchema, type RunCreationRequest } from './contracts.js';

const legacyKey = 'tempo-analysis-command-v1';
// Retain the existing authority slot: commands and run creation must never compete.
const prefix = 'tempo-analysis-command-v2:';
type Command = {command_id:string;[key:string]:unknown};
type SavedRequest = {origin:string;workspace:string;path:string;body:string;request_digest:string};
type PendingCommand = SavedRequest & ({version:2;kind?:never}|{version:3;kind:'command'});
type PendingRun = SavedRequest & {version:3;kind:'run_create';run_id:string};
export type PendingMutation = PendingCommand | PendingRun;

function authorityKey(client:IntelligenceClient) {
  return prefix + JSON.stringify([client.connection.origin,client.connection.workspace]);
}
async function sha256(value:string) {
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
  return 'sha256:'+Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');
}
async function requestDigest(path:string, command:Command) {
  const url=new URL(path,'http://authority.invalid');
  if(!path.startsWith('/v1/analysis/')||url.origin!=='http://authority.invalid'||url.hash)throw new Error('Unsupported saved command target. Pending evidence retained.');
  const run=/^\/v1\/analysis\/runs\/([^/]+)\/commands$/.exec(url.pathname);
  const finding=/^\/v1\/analysis\/findings\/([^/]+)\/commands$/.exec(url.pathname);
  let target:Record<string,unknown>;
  if(run && !url.search) target={run_id:decodeURIComponent(run[1])};
  else if(finding && url.searchParams.get('run_id') && Number.isSafeInteger(Number(url.searchParams.get('generation'))) && Number(url.searchParams.get('generation'))>0 && [...url.searchParams.keys()].sort().join(',')==='generation,run_id') {
    target={run_id:url.searchParams.get('run_id'),finding_id:decodeURIComponent(finding[1]),generation:Number(url.searchParams.get('generation'))};
  } else throw new Error('Unsupported saved command target. Pending evidence retained.');
  const payload={...target,schema_version:1,budget:250,...command};
  return sha256(JSON.stringify(payload,Object.keys(payload).sort()));
}
function blocked(kind?:'command'|'run_create') {
  return new Error(kind==='run_create'
    ? 'Analysis creation is unconfirmed. Recover or retry the existing request before starting another.'
    : 'Recover the pending command before another change.');
}

/** One unresolved mutation per exact authority, persisted before any transmission. */
export class PendingCommands {
  private dispatching=new Map<string,'command'|'run_create'>();
  constructor(private readonly storage:Storage) {}

  private persist(client:IntelligenceClient,saved:PendingMutation) {
    const key=authorityKey(client),serialized=JSON.stringify(saved);
    this.storage.setItem(key,serialized);
    if(this.storage.getItem(key)!==serialized)throw new Error('Unable to retain the request. Nothing was sent.');
  }

  async pending(client:IntelligenceClient):Promise<PendingMutation|null> {
    const storageKey=authorityKey(client);
    const legacyRaw=this.storage.getItem(legacyKey);
    if(legacyRaw) {
      const legacy=JSON.parse(legacyRaw) as {origin:string;workspace:string;path:string;command:Command};
      if(legacy.origin===client.connection.origin && legacy.workspace===client.connection.workspace) {
        const migrated:PendingCommand={version:3,kind:'command',origin:legacy.origin,workspace:legacy.workspace,path:legacy.path,body:JSON.stringify(legacy.command),request_digest:await requestDigest(legacy.path,legacy.command)};
        const existing=this.storage.getItem(storageKey);
        if(existing && existing!==JSON.stringify(migrated))throw new Error('Conflicting saved commands require receipt recovery. Both records retained.');
        this.persist(client,migrated);
        this.storage.removeItem(legacyKey);
      }
    }
    const raw=this.storage.getItem(storageKey);
    if(!raw)return null;
    const saved=JSON.parse(raw) as PendingMutation;
    if(!saved||saved.origin!==client.connection.origin||saved.workspace!==client.connection.workspace||typeof saved.body!=='string'||typeof saved.path!=='string'||typeof saved.request_digest!=='string')throw new Error('Invalid saved request authority. Pending evidence retained.');
    if(saved.version===3 && saved.kind==='run_create') {
      const request=runRequestSchema.parse(JSON.parse(saved.body));
      if(saved.path!=='/v1/analysis/runs'||saved.run_id!==request.run_id||request.workspace_id!==saved.workspace||saved.request_digest!==await sha256(JSON.stringify([saved.path,saved.body])))throw new Error('Saved run creation identity mismatch. Pending evidence retained.');
    } else {
      if(!(saved.version===2&&saved.kind===undefined||saved.version===3&&saved.kind==='command'))throw new Error('Unsupported saved mutation. Pending evidence retained.');
      const command=JSON.parse(saved.body) as Command;
      if(!command||typeof command.command_id!=='string'||saved.request_digest!==await requestDigest(saved.path,command))throw new Error('Saved command identity mismatch. Pending evidence retained.');
    }
    return saved;
  }

  async assertAvailable(client:IntelligenceClient) {
    const sending=this.dispatching.get(authorityKey(client));
    if(sending)throw blocked(sending);
    const saved=await this.pending(client);
    if(saved)throw blocked(saved.kind);
  }

  private async fenced<T>(client:IntelligenceClient,kind:'command'|'run_create',action:()=>Promise<T>) {
    const key=authorityKey(client),sending=this.dispatching.get(key);
    if(sending)throw blocked(sending);
    this.dispatching.set(key,kind);
    try{return await action();}finally{this.dispatching.delete(key);}
  }

  private async authoritative<T extends PendingMutation>(client:IntelligenceClient,saved:T):Promise<T> {
    const current=await this.pending(client);
    if(JSON.stringify(current)!==JSON.stringify(saved))throw new Error('Saved request changed or belongs to another authority. Pending evidence retained.');
    return saved;
  }

  private clear(client:IntelligenceClient,saved:PendingMutation) {
    const storageKey=authorityKey(client);
    if(this.storage.getItem(storageKey)!==JSON.stringify(saved))throw new Error('Saved request changed during recovery. Pending evidence retained.');
    this.storage.removeItem(storageKey);
  }

  private confirm(client:IntelligenceClient,saved:PendingCommand,receipt:ReturnType<typeof commandSchema.parse>) {
    const command=JSON.parse(saved.body) as Command;
    if(receipt.command_id!==command.command_id||receipt.request_digest!==saved.request_digest)throw new Error('Command receipt identity mismatch. Pending evidence retained.');
    this.clear(client,saved);
    return receipt;
  }

  async recover(client:IntelligenceClient,saved:PendingCommand) {
    return this.fenced(client,'command',async()=>{
      await this.authoritative(client,saved);
      const command=JSON.parse(saved.body) as Command;
      const receipt=await client.request(`/v1/analysis/commands/${encodeURIComponent(command.command_id)}`,commandSchema);
      return this.confirm(client,saved,receipt);
    });
  }

  async retry(client:IntelligenceClient,saved:PendingCommand) {
    return this.fenced(client,'command',async()=>{
      await this.authoritative(client,saved);
      const receipt=await client.requestSerialized(saved.path,commandSchema,saved.body);
      return this.confirm(client,saved,receipt);
    });
  }

  async submit(client:IntelligenceClient,path:string,command:Command) {
    return this.fenced(client,'command',async()=>{
      const existing=await this.pending(client);
      if(existing)throw blocked(existing.kind);
      const body=JSON.stringify(command);
      const saved:PendingCommand={version:3,kind:'command',origin:client.connection.origin,workspace:client.connection.workspace,path,body,request_digest:await requestDigest(path,JSON.parse(body) as Command)};
      this.persist(client,saved);
      const receipt=await client.requestSerialized(path,commandSchema,body);
      return this.confirm(client,saved,receipt);
    });
  }

  private verifyRun(saved:PendingRun,status:ReturnType<typeof runStatusSchema.parse>) {
    const intended=runRequestSchema.parse(JSON.parse(saved.body));
    if(status.workspace_id!==saved.workspace||status.run_id!==saved.run_id||status.revision!==intended.revision||JSON.stringify(status.scope)!==JSON.stringify(intended))throw new Error('Analysis creation scope conflicts with Intelligence. Pending evidence retained.');
  }

  private async confirmRun(client:IntelligenceClient,saved:PendingRun) {
    const status=await client.request(`/v1/analysis/runs/${encodeURIComponent(saved.run_id)}`,runStatusSchema);
    this.verifyRun(saved,status);
    this.clear(client,saved);
    return saved.run_id;
  }

  async submitRun(client:IntelligenceClient,request:RunCreationRequest) {
    return this.fenced(client,'run_create',async()=>{
      const existing=await this.pending(client);
      if(existing)throw blocked(existing.kind);
      const run_id=request.run_id??crypto.randomUUID();
      const body=JSON.stringify({...request,run_id});
      const parsed=runRequestSchema.parse(JSON.parse(body));
      if(parsed.workspace_id!==client.connection.workspace)throw new Error('Analysis request belongs to another workspace.');
      const path='/v1/analysis/runs';
      const saved:PendingRun={version:3,kind:'run_create',origin:client.connection.origin,workspace:client.connection.workspace,path,body,run_id,request_digest:await sha256(JSON.stringify([path,body]))};
      this.persist(client,saved);
      const status=await client.requestSerialized(path,runStatusSchema,body);
      this.verifyRun(saved,status);
      return this.confirmRun(client,saved);
    });
  }

  async recoverRun(client:IntelligenceClient,saved:PendingRun) {
    return this.fenced(client,'run_create',async()=>{
      await this.authoritative(client,saved);
      return this.confirmRun(client,saved);
    });
  }

  async retryRun(client:IntelligenceClient,saved:PendingRun) {
    return this.fenced(client,'run_create',async()=>{
      await this.authoritative(client,saved);
      const status=await client.requestSerialized(saved.path,runStatusSchema,saved.body);
      this.verifyRun(saved,status);
      return this.confirmRun(client,saved);
    });
  }
}
