import { Chessground } from "@lichess-org/chessground";
import type { Api } from "@lichess-org/chessground/api";
import { Chess } from "chess.js";
import { gameSourceUrl } from "./game-source.js";
import { mountGameBrowser, comparisonChronologyNotice } from "./game-browser.js";
import { z } from "zod";
import { IntelligenceClient, type Connection } from "./client.js";
import { listSchema, reportSchema, detailSchema, type Finding, type Report } from "./contracts.js";
import { PendingCommands } from "./commands.js";
const key="tempo-analysis-connection-v1";
const labels:Record<string,string>={repertoire_miss:"Repertoire miss",practical_gap:"Practical gap",engine_opportunity:"Engine opportunity"};
function node<K extends keyof HTMLElementTagNameMap>(tag:K,text?:string){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;}
function button(text:string,action:()=>void|Promise<void>){const value=node("button",text);value.type="button";value.onclick=()=>{void action();};return value;}
function field(label:string,value="",type="text"){const wrapper=node("label",label);const input=node("input");input.type=type;input.value=value;wrapper.append(input);return {wrapper,input};}
function select(label:string,choices:Record<string,string>,value:string,onChange:(value:string)=>void){const wrapper=node("label",label);const input=node("select");input.setAttribute("aria-label",label);for(const [id,title]of Object.entries(choices)){const option=node("option",title);option.value=id;input.append(option);}input.value=value;input.onchange=()=>onChange(input.value);wrapper.append(input);return wrapper;}
function record(value:unknown){return value as Record<string,unknown>;}
export function mountWorkbench(root:HTMLElement){
  const commands=new PendingCommands(sessionStorage);
  let client:IntelligenceClient|undefined,board:Api|undefined,renderGeneration=0;
  let closeGameBrowser:(()=>void)|undefined;
  let gameInspectionAvailable=false;
  let runId=new URL(location.href).searchParams.get("run")??"",category=new URL(location.href).searchParams.get("category")??"",dismissal=new URL(location.href).searchParams.get("dismissal")??"",report:Report|undefined;
  const header=node("header");const title=node("div");title.append(node("small","TEMPO WORKBENCH"),node("h1","Your games, understood."));header.append(title,node("span","Analysis"));
  const message=node("p");message.setAttribute("role","status");
  const content=node("main");root.replaceChildren(header,message,content);
  function status(text:string){message.textContent=text;}
  function error(value:unknown){status(value instanceof Error?value.message:"Analysis unavailable. Retry explicitly.");}
  function history(){const url=new URL(location.href);for(const [key,value]of Object.entries({run:runId,category,dismissal})){if(value)url.searchParams.set(key,value);else url.searchParams.delete(key);}window.history.replaceState({},"",url);}
  function connectionForm(saved?:Partial<Connection>){
    renderGeneration++;closeGameBrowser?.();closeGameBrowser=undefined;
    board?.destroy();board=undefined;content.replaceChildren();
    const panel=node("section");panel.className="connection";panel.append(node("h2","Connect to Intelligence"),node("p","Use the caller credential for this workspace. Study connects separately."));
    const origin=field("Intelligence origin",saved?.origin??"http://127.0.0.1:8049"),workspace=field("Workspace",saved?.workspace),credential=field("Caller credential",saved?.credential,"password");
    panel.append(origin.wrapper,workspace.wrapper,credential.wrapper,button("Connect",async()=>{
      status("Checking connection…");
      try{const next=new IntelligenceClient({origin:origin.input.value,workspace:workspace.input.value,credential:credential.input.value});const capabilities=await next.bootstrap();gameInspectionAvailable=capabilities.capabilities.includes("selected-games-v1");client?.disconnect();client=next;sessionStorage.setItem(key,JSON.stringify(next.connection));status("Connected");await home();}catch(value){error(value);}
    }));content.append(panel);
  }
  async function saveCommand(path:string,command:{command_id:string;[key:string]:unknown}){
    const authority=client!;
    return commands.submit(authority,path,command);
  }
  async function pendingCommand(toolbar:HTMLElement){
    const authority=client!;
    const saved=await commands.pending(authority);
    if(!saved)return;
    if(saved.kind==='run_create'){
      try{
        const confirmed=await commands.recoverRun(authority,saved);
        if(client===authority&&toolbar.isConnected){runId=confirmed;history();status('Analysis creation confirmed by Intelligence.');}
      }catch{
        if(client!==authority||!toolbar.isConnected)return;
        status('Analysis creation is unconfirmed. Recover or retry the existing request before starting another.');
        toolbar.append(button('Retry analysis creation',async()=>{
          const generation=renderGeneration;
          try{const confirmed=await commands.retryRun(authority,saved);if(client===authority&&generation===renderGeneration){runId=confirmed;history();status('Analysis creation confirmed by Intelligence.');await home();}}
          catch(value){if(client===authority&&generation===renderGeneration)error(value);}
        }));
      }
      return;
    }
    try{await commands.recover(authority,saved);status("Saved command confirmed by Intelligence.");}
    catch{status("Command persistence is unconfirmed. Recover it before another change.");toolbar.append(button("Retry pending command",async()=>{try{await commands.retry(authority,saved);status("Command confirmed by Intelligence.");if(client===authority)await home();}catch(value){error(value);}}));}
  }
  async function home(){
    const generation=++renderGeneration;closeGameBrowser?.();closeGameBrowser=undefined;board?.destroy();board=undefined;content.replaceChildren();
    const toolbar=node("section");toolbar.className="toolbar";toolbar.append(node("h2","Analysis runs"),button("Refresh",()=>home()),button("Disconnect",()=>{client?.disconnect();client=undefined;sessionStorage.removeItem(key);connectionForm();}));content.append(toolbar);
    try{
      await pendingCommand(toolbar);if(generation!==renderGeneration)return;
      const [runs,inputs]=await Promise.all([client!.request("/v1/analysis/runs",listSchema),client!.request("/v1/analysis/inputs",listSchema)]);
      if(generation!==renderGeneration)return;
      const importPanel=node("section");importPanel.append(node("h3","Explicit game scope"),node("p","Choose an accepted export. Import is a manual operator command; this page does not fetch account history."));
      const inputChoices:Record<string,string>={"":"Choose an imported scope"};for(const item of inputs.items){if(item.state==="accepted")inputChoices[String(item.input_id)+":"+String(item.revision)]=`${String(item.input_id)} · ${(item.game_ids as string[]).length} games · ${String(item.completeness)}`;}
      let selected="";importPanel.append(select("Imported input",inputChoices,"",value=>{selected=value;}),button("Start analysis · 250 decisions",async()=>{
        const authority=client!,generation=renderGeneration;
        try{await commands.assertAvailable(authority);}catch(value){if(client===authority)error(value);return;}
        if(!selected){status("Choose an accepted input first.");return;}
        const input=inputs.items.find(item=>String(item.input_id)+":"+String(item.revision)===selected)!;
        status('Saving analysis request…');
        try{
          const confirmed=await commands.submitRun(authority,{schema_version:1,workspace_id:authority.connection.workspace,revision:1,input_id:String(input.input_id),input_revision:Number(input.revision),position_budget:250});
          if(client!==authority||generation!==renderGeneration)return;
          runId=confirmed;history();status('Request saved. Findings and engine progress are separate.');await home();
        }catch(value){
          if(client!==authority||generation!==renderGeneration)return;
          error(value);
          try{
            const pending=await commands.pending(authority);
            if(pending?.kind==='run_create'){
              await pendingCommand(toolbar);
              if(client===authority&&generation===renderGeneration&&runId===pending.run_id)await home();
            }
          }catch(persistenceError){if(client===authority&&generation===renderGeneration)error(persistenceError);}
        }
      }));content.append(importPanel);
      const choices:Record<string,string>={"":"Choose a run"};for(const item of runs.items)choices[String(item.run_id)]=`${String(item.run_id)} · ${String(item.state)}`;
      content.append(select("Run",choices,runId,value=>{runId=value;history();void home();}));
      if(!runId)return;
      const run=runs.items.find(item=>item.run_id===runId);if(!run){status("Run not found in this workspace.");return;}
      const progress=node("section");progress.className="progress";progress.append(node("p",`Ranking: ${String(record(run.scope).ranking_source??"actual")} · ${String(record(run.scope).ranking_evidence_id??"distinct selected games")}`));progress.append(node("h3","Scope and progress"),node("p",`Input ${String(record(run.scope).input_id)} · ${String(run.state)}`),node("p",Object.entries(record(run.progress)).map(([name,count])=>`${count} ${name}`).join(" · ")));
      const legacyRun=run.derivation_version!==2||run.recovery_required==="legacy_analysis_revision_required";
      if(legacyRun)progress.append(node("p","This older analysis requires a new revision before continuing. Cancel remains available."));
      for(const action of (legacyRun?["cancel"]:["continue","retry","cancel","resume"])){progress.append(button(action==="continue"?"Continue · next 250 decisions":action[0].toUpperCase()+action.slice(1),async()=>{const command={schema_version:1,command_id:crypto.randomUUID(),action,expected_revision:run.revision,budget:250};status("Saving command…");try{await saveCommand(`/v1/analysis/runs/${encodeURIComponent(runId)}/commands`,command);status("Command confirmed; work may still be pending.");await home();}catch(value){error(value);}}));}if(gameInspectionAvailable)progress.append(button("Browse selected games",()=>{renderGeneration++;board?.destroy();board=undefined;status("Selected game scope");const scope=record(run.scope);closeGameBrowser=mountGameBrowser(content,client!,runId,{revision:Number(run.revision),input_id:String(scope.input_id),input_revision:Number(scope.input_revision)},()=>{void home();},error);}));else progress.append(node("p","Selected-game browsing is unavailable with this Intelligence version."));content.append(progress);
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
      const completion=next.complete===true?"Analysis complete":next.complete===false?"Analysis incomplete":"Analysis completeness unknown";
      const state=node("p",`${completion} · ${String(record(next.source_provenance??{}).kind??"explicit source")} · ${next.status} · ${String(next.source_completeness??"unknown")} inputs · ${String(next.unprocessed_count??"unknown")} engine decisions unprocessed · ${String(next.insufficient_quality_count??0)} searches need better evidence`);state.className="report-state";target.append(state);
      const scope=record(next.scope??{});const selectedGames=Array.isArray(scope.game_ids)?scope.game_ids.length:"unknown";
      target.append(node("p",comparisonChronologyNotice));
      target.append(node("p",`${selectedGames} selected games · ${String(next.unknown_coverage_count??"unknown")} comparison decisions unknown.`));
      const selections=Array.isArray(scope.selections)?scope.selections:[];const titles=[...new Set(selections.map(value=>{const item=record(value);return String(item.repertoire_title??item.repertoire_id??"unknown");}))];if(titles.length)target.append(node("p",`Compared repertoires: ${titles.join(", ")}. Other policies remain outside this report.`));
      const unknown=Array.isArray(next.unknown_coverage)?next.unknown_coverage:[];
      if(unknown.length){const uncertainty=node("details");uncertainty.append(node("summary","Positions requiring mapping or coverage verification"));for(const value of unknown){const item=record(value);uncertainty.append(node("p",`Game ${String(item.game_id??"unknown")} · ply ${String(item.ply??"unknown")} · ${String(item.reason??"unknown").replaceAll("_"," ")}`));}target.append(uncertainty);}
      if(Array.isArray(next.reasons)&&next.reasons.includes("legacy_analysis_revision_required"))target.append(node("p","This older analysis needs a new revision before work can continue."));
      if(Array.isArray(next.reasons)&&next.reasons.includes("incompatible_engine_profile"))target.append(node("p","The engine changed during this comparison. Start a new analysis revision with the selected engine profile. Earlier results are preserved."));
      if(next.derivation_version!==2)target.append(node("p","Legacy report: engine repertoire associations are unverified. Create a new analysis revision to verify them."));
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
      const info=node("div");info.append(node("small",labels[finding.category]),node("h2",`Game ${finding.representative.game_id??"population evidence"} · ply ${finding.representative.ply??"—"}`),node("p",`Played: ${finding.representative.actual_move??"unavailable"}`),node("p",`Accepted: ${finding.expected_moves.join(", ")||"no accepted response established"}`),node("p",`Evidence: ${finding.evidence_status} · ${finding.dismissal_state}`),node("p",`Supporting games: ${finding.supporting_games.join(", ")||"none; model/population evidence"}`),node("p",finding.reason_codes.map(reason=>reason.replaceAll("_"," ")).join(" · ")));
      info.append(node("p",comparisonChronologyNotice));
      if(finding.category==="practical_gap"){
        const move=typeof finding.opponent_move==="string"?finding.opponent_move:null;
        const fen=typeof finding.opponent_fen==="string"?finding.opponent_fen:null;
        const reply=move&&fen?new Chess(fen).move(move).san:move??"unknown";
        info.append(node("p",`Uncovered opponent reply: ${reply}. No accepted continuation under this policy.`));
      }
      const sourceUrl=gameSourceUrl(finding.representative.provider,finding.representative.game_url);
      if(sourceUrl){const sourceLink=node("a","Open original game");sourceLink.href=sourceUrl;sourceLink.target="_blank";sourceLink.rel="noopener noreferrer";info.append(sourceLink);}
      info.append(node("p",`Repertoire: ${String(finding.repertoire_title??finding.repertoire_id??"unknown")} · Policy: ${String(finding.policy_id??"unknown")}`));
      info.append(node("p",`Move ${finding.representative.fen.split(" ")[5]}${finding.representative.learner_color==="black"?"…":"."} · ${finding.representative.learner_color} to play. Publication and activation evidence are frozen as exported; a proposed response still requires separate Content approval and Learning activation.`));
      const encounterCount=finding.eligible_encounter_games;
      info.append(node("p",typeof encounterCount==="number"?`Observed in ${finding.supporting_game_count} of ${encounterCount} eligible selected games.`:"Eligible encounter denominator: unknown."));
      info.append(node("p",finding.category==="repertoire_miss"?`Review existing ${finding.learning_card_id?`Learning card ${String(finding.learning_card_id)} (Content ${String(finding.card_id??"unknown")})`:`Content card ${String(finding.card_id??"unknown")}; physical Learning identity unavailable`}; a repertoire miss does not establish an engine mistake.`:finding.category==="practical_gap"?"Review a proposed continuation through Content before adding training content.":"Informational engine opportunity; repertoire relevance may be unknown."));
      if(Array.isArray(finding.related_findings)&&finding.related_findings.length){info.append(node("h3","Other evidence for this incident"));for(const related of finding.related_findings){const item=record(related);info.append(button(labels[String(item.category)]??"Related finding",()=>detail({...finding,finding_id:String(item.finding_id)})));}}
      info.append(button(finding.dismissal_state==="dismissed"?"Reopen":"Dismiss",async()=>{const action=finding.dismissal_state==="dismissed"?"reopen":"dismiss";const command={schema_version:1,command_id:crypto.randomUUID(),action,expected_revision:finding.finding_revision};status("Saving dismissal state…");try{await saveCommand(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/commands?${query}`,command);status("State confirmed. The updated report is pending publication.");await home();}catch(value){error(value);}}));
      info.append(button("Export informational context",async()=>{try{const result=await client!.request(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/export?${query}&kind=context`,z.object({workspace_id:z.string()}).passthrough());const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const link=node("a");link.href=url;link.download="tempo-gap-context.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch(value){error(value);}}));
      info.append(node("p",`Source: ${String(finding.representative.provider??"export")} · Learner: ${String(finding.representative.learner_id??"identity unavailable")} · Played at: ${String(finding.representative.played_at??"time unknown")}`));
      info.append(node("p",`Remediation delivery: ${String(finding.delivery_status??"unknown").replaceAll("_"," ")} · Study settlement: ${String(finding.learning_settlement??"unknown")}`));
      if(finding.engine){const engine=record(finding.engine);const best=record(engine.best??{});const played=record(engine.played??{});info.append(node("p",`Best continuation: ${Array.isArray(best.moves)?best.moves.join(" "):"pending"}`));const score=(value:Record<string,unknown>)=>{const point=record(value.score??{});return point.kind==="mate"?`mate ${String(point.value)}`:point.kind==="centipawns"?`${String(point.value)} cp`:"pending";};info.append(node("p",`Search: ${score(best)} best · ${score(played)} played · completed depth ${String(best.depth??"unknown")} · learner perspective`));}
      if(report?.derivation_version===2&&finding.actions.response_proposal?.available){info.append(button("Export response draft",async()=>{try{const result=await client!.request(`/v1/analysis/findings/${encodeURIComponent(finding.finding_id)}/export?${query}&kind=response-proposal`,z.object({workspace_id:z.string(),status:z.literal("draft")}).passthrough());const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const link=node("a");link.href=url;link.download="tempo-response-proposal.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),0);}catch(value){error(value);}}));}else info.append(node("p",`Response proposal unavailable: ${finding.actions.response_proposal?.reason==="validated_A_proposal_unavailable"?"a validated response draft is required":finding.actions.response_proposal?.reason?.replaceAll("_"," ")??"validated response line required"}. Export does not approve or activate content.`));
      if(report?.derivation_version!==2)info.append(node("p","Legacy report: engine repertoire associations are unverified. Start a new analysis revision before using them."));
      if(finding.lineage===null)info.append(node("p","Repertoire, policy and source lineage are unknown for this engine finding."));
      const provenance=node("details");provenance.append(node("summary","Provenance and uncertainty"),node("pre",JSON.stringify(finding,null,2)));info.append(provenance);layout.append(info);content.append(layout);
      board=Chessground(boardElement,{fen:finding.representative.fen,orientation,viewOnly:true,coordinates:true,animation:{enabled:false}});
      if(finding.engine){const moves=record(record(finding.engine).best??{}).moves;if(Array.isArray(moves)&&typeof moves[0]==="string")boardActions.append(button("Show engine move",()=>{chess.load(finding.representative.fen);chess.move(moves[0]);board!.set({fen:chess.fen()});}));}
      boardActions.append(button("Flip board",()=>{orientation=orientation==="white"?"black":"white";board!.set({orientation});}),button("Decision position",()=>{chess.load(finding.representative.fen);board!.set({fen:chess.fen()});}),button("Show played move",()=>{chess.load(finding.representative.fen);if(finding.representative.actual_move){chess.move(finding.representative.actual_move);board!.set({fen:chess.fen()});}}),button("Show response",()=>{try{chess.load(finding.representative.fen);if(candidate){chess.move(candidate);board!.set({fen:chess.fen()});}else status("Response analysis unavailable or pending.");}catch(value){error(value);}}));
    }catch(value){error(value);}
  }
  try{const saved=JSON.parse(sessionStorage.getItem(key)??"null") as Connection|null;if(saved){client=new IntelligenceClient(saved);void client.bootstrap().then(capabilities=>{gameInspectionAvailable=capabilities.capabilities.includes("selected-games-v1");return home();}).catch(value=>{connectionForm(saved);error(value);});}else connectionForm();}catch{connectionForm();}
  const navigate=()=>{const url=new URL(location.href);runId=url.searchParams.get("run")??"";if(client)void home();};
  window.addEventListener("popstate",navigate);
  return ()=>{renderGeneration++;window.removeEventListener("popstate",navigate);closeGameBrowser?.();board?.destroy();client?.disconnect();};
}
