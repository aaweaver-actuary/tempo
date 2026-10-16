import { Chessground } from "@lichess-org/chessground";
import type { Api } from "@lichess-org/chessground/api";
import { Chess } from "chess.js";
import { z } from "zod";
import { IntelligenceClient, type Connection } from "./client.js";
import { listSchema, reportSchema, detailSchema, commandSchema, type Finding, type Report } from "./contracts.js";
const key="tempo-analysis-connection-v1",pendingKey="tempo-analysis-command-v1";
const labels:Record<string,string>={repertoire_miss:"Repertoire miss",practical_gap:"Practical gap",engine_opportunity:"Engine opportunity"};
function node<K extends keyof HTMLElementTagNameMap>(tag:K,text?:string){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;}
function button(text:string,action:()=>void|Promise<void>){const value=node("button",text);value.type="button";value.onclick=()=>{void action();};return value;}
function field(label:string,value="",type="text"){const wrapper=node("label",label);const input=node("input");input.type=type;input.value=value;wrapper.append(input);return {wrapper,input};}
function select(label:string,choices:Record<string,string>,value:string,onChange:(value:string)=>void){const wrapper=node("label",label);const input=node("select");input.setAttribute("aria-label",label);for(const [id,title]of Object.entries(choices)){const option=node("option",title);option.value=id;input.append(option);}input.value=value;input.onchange=()=>onChange(input.value);wrapper.append(input);return wrapper;}
function record(value:unknown){return value as Record<string,unknown>;}
export function mountWorkbench(root:HTMLElement){
  let client:IntelligenceClient|undefined,board:Api|undefined,renderGeneration=0;
  let runId=new URL(location.href).searchParams.get("run")??"",category=new URL(location.href).searchParams.get("category")??"",dismissal=new URL(location.href).searchParams.get("dismissal")??"",report:Report|undefined;
  const header=node("header");const title=node("div");title.append(node("small","TEMPO WORKBENCH"),node("h1","Your games, understood."));header.append(title,node("span","Analysis"));
  const message=node("p");message.setAttribute("role","status");
  const content=node("main");root.replaceChildren(header,message,content);
  function status(text:string){message.textContent=text;}
  function error(value:unknown){status(value instanceof Error?value.message:"Analysis unavailable. Retry explicitly.");}
  function history(){const url=new URL(location.href);for(const [key,value]of Object.entries({run:runId,category,dismissal})){if(value)url.searchParams.set(key,value);else url.searchParams.delete(key);}window.history.replaceState({},"",url);}
  function connectionForm(saved?:Partial<Connection>){
    board?.destroy();board=undefined;content.replaceChildren();
    const panel=node("section");panel.className="connection";panel.append(node("h2","Connect to Intelligence"),node("p","Use the caller credential for this workspace. Study connects separately."));
    const origin=field("Intelligence origin",saved?.origin??"http://127.0.0.1:8049"),workspace=field("Workspace",saved?.workspace),credential=field("Caller credential",saved?.credential,"password");
    panel.append(origin.wrapper,workspace.wrapper,credential.wrapper,button("Connect",async()=>{
      status("Checking connection…");
      try{const next=new IntelligenceClient({origin:origin.input.value,workspace:workspace.input.value,credential:credential.input.value});await next.bootstrap();client?.disconnect();client=next;sessionStorage.setItem(key,JSON.stringify(next.connection));status("Connected");await home();}catch(value){error(value);}
    }));content.append(panel);
  }
  async function saveCommand(path:string,command:{command_id:string;[key:string]:unknown}){
    sessionStorage.setItem(pendingKey,JSON.stringify({origin:client!.connection.origin,workspace:client!.connection.workspace,path,command}));
    const receipt=await client!.request(path,commandSchema,command);
    if(receipt.command_id!==command.command_id)throw new Error("Command receipt identity mismatch");
    sessionStorage.removeItem(pendingKey);return receipt;
  }
  async function pendingCommand(toolbar:HTMLElement){
    const saved=JSON.parse(sessionStorage.getItem(pendingKey)??"null") as {origin:string;workspace:string;path:string;command:{command_id:string;[key:string]:unknown}}|null;
    if(!saved||saved.origin!==client!.connection.origin||saved.workspace!==client!.connection.workspace)return;
    try{const receipt=await client!.request(`/v1/analysis/commands/${encodeURIComponent(saved.command.command_id)}`,commandSchema);if(receipt.command_id!==saved.command.command_id)throw new Error("Command receipt identity mismatch");sessionStorage.removeItem(pendingKey);status("Saved command confirmed by Intelligence.");}
    catch{status("Command persistence is unconfirmed. Current server state is shown below.");toolbar.append(button("Retry pending command",async()=>{try{await saveCommand(saved.path,saved.command);status("Command confirmed by Intelligence.");await home();}catch(value){error(value);}}));}
  }
  async function home(){
    const generation=++renderGeneration;board?.destroy();board=undefined;content.replaceChildren();
    const toolbar=node("section");toolbar.className="toolbar";toolbar.append(node("h2","Analysis runs"),button("Refresh",()=>home()),button("Disconnect",()=>{client?.disconnect();client=undefined;sessionStorage.removeItem(key);connectionForm();}));content.append(toolbar);
    try{
      await pendingCommand(toolbar);if(generation!==renderGeneration)return;
      const [runs,inputs]=await Promise.all([client!.request("/v1/analysis/runs",listSchema),client!.request("/v1/analysis/inputs",listSchema)]);
      if(generation!==renderGeneration)return;
      const importPanel=node("section");importPanel.append(node("h3","Explicit game scope"),node("p","Choose an accepted export. Import is a manual operator command; this page does not fetch account history."));
      const inputChoices:Record<string,string>={"":"Choose an imported scope"};for(const item of inputs.items){if(item.state==="accepted")inputChoices[String(item.input_id)+":"+String(item.revision)]=`${String(item.input_id)} · ${(item.game_ids as string[]).length} games · ${String(item.completeness)}`;}
      let selected="";importPanel.append(select("Imported input",inputChoices,"",value=>{selected=value;}),button("Start analysis · 250 decisions",async()=>{
        if(!selected){status("Choose an accepted input first.");return;}
        const input=inputs.items.find(item=>String(item.input_id)+":"+String(item.revision)===selected)!;
        const id=crypto.randomUUID();const payload={schema_version:1,workspace_id:client!.connection.workspace,run_id:id,revision:1,input_id:input.input_id,input_revision:input.revision,position_budget:250};
        status("Saving analysis request…");try{const saved=await client!.request("/v1/analysis/runs",z.object({schema_version:z.literal(1),workspace_id:z.string(),run_id:z.string()}).passthrough(),payload);if(saved.run_id!==id)throw new Error("Created run identity mismatch");runId=id;history();status("Request saved. Findings and engine progress are separate.");await home();}catch(value){error(value);}
      }));content.append(importPanel);
      const choices:Record<string,string>={"":"Choose a run"};for(const item of runs.items)choices[String(item.run_id)]=`${String(item.run_id)} · ${String(item.state)}`;
      content.append(select("Run",choices,runId,value=>{runId=value;history();void home();}));
      if(!runId)return;
      const run=runs.items.find(item=>item.run_id===runId);if(!run){status("Run not found in this workspace.");return;}
      const progress=node("section");progress.className="progress";progress.append(node("p",`Ranking: ${String(record(run.scope).ranking_source??"actual")} · ${String(record(run.scope).ranking_evidence_id??"distinct selected games")}`));progress.append(node("h3","Scope and progress"),node("p",`Input ${String(record(run.scope).input_id)} · ${String(run.state)}`),node("p",Object.entries(record(run.progress)).map(([name,count])=>`${count} ${name}`).join(" · ")));
      for(const action of ["continue","retry","cancel","resume"]){progress.append(button(action==="continue"?"Continue · next 250 decisions":action[0].toUpperCase()+action.slice(1),async()=>{const command={schema_version:1,command_id:crypto.randomUUID(),action,expected_revision:run.revision,budget:250};status("Saving command…");try{await saveCommand(`/v1/analysis/runs/${encodeURIComponent(runId)}/commands`,command);status("Command confirmed; work may still be pending.");await home();}catch(value){error(value);}}));}content.append(progress);
      const filters=node("section");filters.className="filters";filters.append(select("Category",{"":"All categories",...labels},category,value=>{category=value;history();void load();}),select("Dismissal",{"":"All findings",open:"Open",dismissed:"Dismissed"},dismissal,value=>{dismissal=value;history();void load();}));content.append(filters);
      const findings=node("div");findings.id="findings";content.append(findings);await load();
    }catch(value){error(value);}
  }
  async function load(after?:string){
    const generation=++renderGeneration;const target=document.getElementById("findings");if(!target)return;
    try{
      const query=new URLSearchParams();if(category)query.set("category",category);if(dismissal)query.set("dismissal",dismissal);if(after)query.set("after",after);
      const next=await client!.request(`/v1/analysis/runs/${encodeURIComponent(runId)}/report?${query}`,reportSchema);if(generation!==renderGeneration)return;if(next.run_id!==runId)throw new Error("Report identity mismatch");report=next;
      if(!after)target.replaceChildren();else target.querySelector(".more")?.remove();
      const state=node("p",`${String(record(next.source_provenance??{}).kind??"explicit source")} · ${next.status} · ${String(next.source_completeness??"inputs pending")} · ${String(next.unprocessed_count??"unknown")} engine decisions unprocessed · ${String(next.insufficient_quality_count??0)} searches need better evidence`);state.className="report-state";target.append(state);
      if(next.items.length===0)target.append(node("p",next.status==="pending"?"Report pending. Run the manual worker or refresh after publication.":"No findings in this page and filter. Unprocessed or unknown scope is shown separately."));
      const grid=node("div");grid.className="finding-grid";for(const finding of next.items){const card=node("article");card.append(node("small",labels[finding.category]),node("h3",finding.category==="practical_gap"?"A response is missing":finding.category==="repertoire_miss"?"An accepted response was missed":"A stronger continuation was found"),node("p",`${finding.supporting_game_count} supporting game${finding.supporting_game_count===1?"":"s"} · ${finding.dismissal_state}`),node("p",`Played ${finding.representative.actual_move??"unavailable"} · ${finding.evidence_status}`),button("Inspect finding",()=>detail(finding)));grid.append(card);}target.append(grid);
      if(next.next_cursor){const more=button("More findings",()=>load(next.next_cursor!));more.className="more";target.append(more);}
    }catch(value){error(value);}
  }
  async function detail(selected:Finding){
    const generation=++renderGeneration;
    try{
      const query=new URLSearchParams({run_id:runId,generation:String(report!.generation)});const response=await client!.request(`/v1/analysis/findings/${encodeURIComponent(selected.finding_id)}?${query}`,detailSchema);
      if(generation!==renderGeneration)return;if(response.run_id!==runId||response.generation!==report!.generation||response.finding.finding_id!==selected.finding_id)throw new Error("Finding identity mismatch");const finding=response.finding;
      board?.destroy();content.replaceChildren(button("Back to findings",()=>home()));
      const layout=node("section");layout.className="detail";const visual=node("div");const boardElement=node("div");boardElement.className="board cg-wrap";boardElement.setAttribute("aria-label","Finding chessboard");visual.append(boardElement);layout.append(visual);
      let orientation=finding.representative.learner_color;const chess=new Chess(finding.representative.fen);const candidate=finding.expected_moves[0]??String(record(record(finding.engine??{}).best??{}).moves instanceof Array?(record(record(finding.engine??{}).best??{}).moves as string[])[0]:"");
      const boardActions=node("div");boardActions.className="board-actions";visual.append(boardActions);
      const info=node("div");info.append(node("small",labels[finding.category]),node("h2",`Game ${finding.representative.game_id??"population evidence"} · ply ${finding.representative.ply??"—"}`),node("p",`Played: ${finding.representative.actual_move??"unavailable"}`),node("p",`Accepted: ${finding.expected_moves.join(", ")||"no accepted response established"}`),node("p",`Evidence: ${finding.evidence_status} · ${finding.dismissal_state}`),node("p",`Supporting games: ${finding.supporting_games.join(", ")||"none; model/population evidence"}`),node("p",finding.reason_codes.join(" · ")));
      if(Array.isArray(finding.related_findings)&&finding.related_findings.length){info.append(node("h3","Other evidence for this incident"));for(const related of finding.related_findings){const item=record(related);info.append(button(labels[String(item.category)]??"Related finding",()=>detail({...finding,finding_id:String(item.finding_id)})));}}
      info.append(button(finding.dismissal_state==="dismissed"?"Reopen":"Dismiss",async()=>{const action=finding.dismissal_state==="dismissed"?"reopen":"dismiss";const command={schema_version:1,command_id:crypto.randomUUID(),action,expected_revision:finding.finding_revision};status("Saving dismissal state…");try{await saveCommand(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/commands?${query}`,command);status("State confirmed. The updated report is pending publication.");await home();}catch(value){error(value);}}));
      info.append(button("Export informational context",async()=>{try{const result=await client!.request(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/export?${query}&kind=context`,z.object({workspace_id:z.string()}).passthrough());const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const link=node("a");link.href=url;link.download="tempo-gap-context.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch(value){error(value);}}));
      info.append(node("p",`Source: ${String(finding.representative.provider??"export")} · Learner: ${String(finding.representative.learner_id??"identity unavailable")} · Played at: ${String(finding.representative.played_at??"time unknown")}`));
      info.append(node("p",`Remediation delivery: ${String(finding.delivery_status??"unknown")} · Study settlement: ${String(finding.learning_settlement??"unknown")}`));
      if(finding.engine){const engine=record(finding.engine);const best=record(engine.best??{});const played=record(engine.played??{});info.append(node("p",`Best continuation: ${Array.isArray(best.moves)?best.moves.join(" "):"pending"}`));const score=(value:Record<string,unknown>)=>{const point=record(value.score??{});return point.kind==="mate"?`mate ${String(point.value)}`:point.kind==="centipawns"?`${String(point.value)} cp`:"pending";};info.append(node("p",`Search: ${score(best)} best · ${score(played)} played · completed depth ${String(best.depth??"unknown")} · learner perspective`));}
      if(finding.actions.response_proposal?.available){info.append(button("Export response draft",async()=>{try{const result=await client!.request(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/export?${query}&kind=response-proposal`,z.object({workspace_id:z.string(),status:z.literal("draft")}).passthrough());const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const link=node("a");link.href=url;link.download="tempo-response-proposal.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch(value){error(value);}}));}else info.append(node("p",`Response proposal unavailable: ${finding.actions.response_proposal?.reason?.replaceAll("_"," ")??"validated response line required"}. Export does not approve or activate content.`));
      const provenance=node("details");provenance.append(node("summary","Provenance and uncertainty"),node("pre",JSON.stringify(finding,null,2)));info.append(provenance);layout.append(info);content.append(layout);
      board=Chessground(boardElement,{fen:finding.representative.fen,orientation,viewOnly:true,coordinates:true,animation:{enabled:false}});
      if(finding.engine){const moves=record(record(finding.engine).best??{}).moves;if(Array.isArray(moves)&&typeof moves[0]==="string")boardActions.append(button("Show engine move",()=>{chess.load(finding.representative.fen);chess.move(moves[0]);board!.set({fen:chess.fen()});}));}
      boardActions.append(button("Flip board",()=>{orientation=orientation==="white"?"black":"white";board!.set({orientation});}),button("Decision position",()=>{chess.load(finding.representative.fen);board!.set({fen:chess.fen()});}),button("Show played move",()=>{chess.load(finding.representative.fen);if(finding.representative.actual_move){chess.move(finding.representative.actual_move);board!.set({fen:chess.fen()});}}),button("Show response",()=>{try{chess.load(finding.representative.fen);if(candidate){chess.move(candidate);board!.set({fen:chess.fen()});}else status("Response analysis unavailable or pending.");}catch(value){error(value);}}));
    }catch(value){error(value);}
  }
  try{const saved=JSON.parse(sessionStorage.getItem(key)??"null") as Connection|null;if(saved){client=new IntelligenceClient(saved);void client.bootstrap().then(()=>home()).catch(value=>{connectionForm(saved);error(value);});}else connectionForm();}catch{connectionForm();}
  const navigate=()=>{const url=new URL(location.href);runId=url.searchParams.get("run")??"";if(client)void home();};
  window.addEventListener("popstate",navigate);
  return ()=>{renderGeneration++;window.removeEventListener("popstate",navigate);board?.destroy();client?.disconnect();};
}
