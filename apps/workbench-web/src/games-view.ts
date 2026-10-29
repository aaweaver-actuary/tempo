import type { BoardAppearance } from "@tempo/board-ui";
/** Games-only composition: existing run reader + existing game browser, no commands. */
import type { GamesReader } from "./games-reader.js";
import { mountGameBrowser } from "./game-browser.js";

export const frozenGamesNotice="These comparisons belong to the accepted input snapshot, not today's live repertoire. A fresh current-repertoire comparison is a separate explicit operation and is unavailable in this viewer.";

export function mountGamesView(target:HTMLElement,reader:GamesReader,runId:string,onRun:(run:string)=>void,onError:(error:unknown)=>void,options:{appearance?:BoardAppearance}={}){
  let active=true,closeBrowser:(()=>void)|undefined;
  const element=(tag:"h2"|"p"|"label"|"div",text:string)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  target.replaceChildren(element("h2","Games · read-only snapshot review"),element("p",frozenGamesNotice));
  const panel=element("div","");target.append(panel);
  async function load(){
    try{
      const runs=await reader.listScopes();
      if(!active)return;
      const label=element("label","Existing game scope");const select=document.createElement("select");
      select.setAttribute("aria-label","Existing game scope");const empty=document.createElement("option");empty.value="";empty.textContent="Choose an existing run";select.append(empty);
      for(const run of runs){
        if(typeof run.run_id!=="string"||!run.run_id)throw new Error("Invalid run identity");
        const option=document.createElement("option");option.value=run.run_id;option.textContent=run.run_id;select.append(option);
      }
      select.value=runId;select.onchange=()=>onRun(select.value);label.append(select);target.insertBefore(label,panel);
      if(!runId){panel.append(element("p",runs.length?"Choose an existing scope to browse its games. Optional engine progress does not prevent viewing.":"No existing game scope is available. Viewing never creates an analysis run."));return;}
      const scope=await reader.readScope(runId);if(!active)return;
      target.insertBefore(element("p",`Run ${runId} · revision ${scope.revision} · accepted input ${scope.input_id} · input revision ${scope.input_revision}`),panel);
      closeBrowser=mountGameBrowser(panel,reader,runId,scope,()=>onRun(""),onError,{backLabel:"Choose another game scope",appearance:options.appearance});
    }catch(error){if(active)onError(error);}
  }
  void load();return ()=>{active=false;closeBrowser?.();};
}
