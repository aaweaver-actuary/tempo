import { z } from "zod";
import { bootstrapSchema } from "./contracts.js";
import { gameScopeSchema, gamePageSchema, type GamesReader, type GameScope } from './games-reader.js';
export type Connection={origin:string;workspace:string;credential:string};
export class IntelligenceClient implements GamesReader {
  private generation=0;
  private active=true;
  private viewingOnly=false;
  get readOnly(){return this.viewingOnly;}
  constructor(readonly connection:Connection, private delivery:typeof fetch=fetch){
    const url=new URL(connection.origin);
    if(!["http:","https:"].includes(url.protocol)||url.origin!==connection.origin||url.username||url.password||!connection.workspace.trim()||!connection.credential)throw new Error("Enter an exact Intelligence origin, workspace, and caller credential.");
  }
  disconnect(){this.generation++;this.active=false;}
  async request<T>(path:string,schema:z.ZodType<T>,body?:unknown):Promise<T>{
    return this.requestSerialized(path,schema,body===undefined?undefined:JSON.stringify(body));
  }
  async requestSerialized<T>(path:string,schema:z.ZodType<T>,body?:string):Promise<T>{
    if(!this.active)throw new Error("Reconnect before using Analysis.");
    if(this.viewingOnly&&body!==undefined)throw new Error("This Intelligence connection is read-only.");
    if(!path.startsWith("/v1/analysis/")||new URL(path,this.connection.origin).origin!==this.connection.origin)throw new Error("Unsupported Analysis path");
    const generation=this.generation;
    const response=await this.delivery.call(globalThis,this.connection.origin+path,{method:body===undefined?"GET":"POST",headers:{Authorization:`Bearer ${this.connection.credential}`,"X-Tempo-Workspace":this.connection.workspace,...(body===undefined?{}:{"Content-Type":"application/json"})},body,credentials:"omit",redirect:"error",cache:"no-store"});
    if(generation!==this.generation)throw new Error("Connection changed; response ignored.");
    if(!response.ok){const error=await response.json().catch(()=>({error:"service_unavailable"}));throw new Error(response.status===401||response.status===403?"Reconnect to Intelligence: access was rejected.":String(error.error??"Analysis unavailable"));}
    const result=schema.parse(await response.json());
    if(generation!==this.generation)throw new Error("Connection changed; response ignored.");
    if((result as {workspace_id?:string}).workspace_id!==this.connection.workspace)throw new Error("Intelligence returned another workspace.");
    return result;
  }
  async listScopes(){
    const schema=z.object({workspace_id:z.string(),items:z.array(gameScopeSchema).max(100)});
    return (await this.request('/v1/analysis/game-scopes',schema)).items;
  }
  async readScope(runId:string){
    const schema=gameScopeSchema.extend({workspace_id:z.string()});
    const scope=await this.request(`/v1/analysis/game-scopes/${encodeURIComponent(runId)}`,schema);
    if(scope.run_id!==runId)throw new Error('Run scope identity mismatch');
    return gameScopeSchema.parse(scope);
  }
  async readGames(scope:GameScope,after?:string){
    const query=after?`?after=${encodeURIComponent(after)}`:'';
    const page=await this.request(`/v1/analysis/runs/${encodeURIComponent(scope.run_id)}/games${query}`,gamePageSchema);
    if(page.run_id!==scope.run_id||page.revision!==scope.revision||page.input_id!==scope.input_id||page.input_revision!==scope.input_revision)throw new Error('Game scope identity mismatch');
    return page;
  }
  async bootstrap(){const value=await this.request("/v1/analysis/bootstrap",bootstrapSchema);if(value.policy_version!=="c-analysis-v1"||!value.capabilities.includes("analysis-v1"))throw new Error("Intelligence Analysis version is incompatible.");this.viewingOnly=value.capabilities.includes("games-readonly-v1");return value;}
}
