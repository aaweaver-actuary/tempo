import { Chessground } from "@lichess-org/chessground";
import type { Api } from "@lichess-org/chessground/api";
import { Chess } from "chess.js";
import { z } from "zod";
import { IntelligenceClient } from "./client.js";
import { gameSourceUrl } from "./game-source.js";

export const comparisonChronologyNotice="Repertoire comparisons use the exported snapshot. Repertoire availability and prior study at game time are unverified.";

const contextSchema=z.object({ply:z.number().int().nonnegative(),repertoire_id:z.string(),policy_id:z.string(),status:z.enum(["matched","missed","unknown"]),reason:z.string(),coverage:z.string(),expected_moves:z.array(z.string())}).passthrough();
const gameSchema=z.object({game_id:z.string(),provider:z.string().nullable().optional(),provider_game_id:z.string().nullable().optional(),game_url:z.string().nullable().optional(),played_at:z.string().nullable(),learner_color:z.enum(["white","black"]),starting_fen:z.string(),moves:z.array(z.string()).max(1000),contexts:z.array(contextSchema).max(2000)}).passthrough();
const pageSchema=z.object({schema_version:z.literal(1),workspace_id:z.string(),run_id:z.string(),revision:z.number().int().positive(),input_id:z.string(),input_revision:z.number().int().positive(),game_count:z.number().int().nonnegative(),selections:z.array(z.object({repertoire_id:z.string(),policy_id:z.string(),repertoire_title:z.string().nullish()}).passthrough()).max(32),items:z.array(gameSchema).max(10),next_after:z.string().nullable()});
type Game=z.infer<typeof gameSchema>;
function node<K extends keyof HTMLElementTagNameMap>(tag:K,text?:string){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;return element;}
function button(title:string,action:()=>void){const element=node("button",title);element.type="button";element.onclick=action;return element;}

/** Legal display positions only; comparison authority comes from accepted contexts. */
export function gameFrames(startingFen:string,moves:string[]){
  const chess=new Chess(startingFen);const frames=[{fen:chess.fen({forceEnpassantSquare:true}),label:"Start"}];
  for(const move of moves){
    const number=chess.fen().split(" ")[5],separator=chess.turn()==="w"?".":"…";
    const played=chess.move(move);const fen=chess.fen({forceEnpassantSquare:true}).split(" ");
    // Standard PGN FEN retains the target after every legal double pawn move;
    // chess.js internally omits it when no adjacent capturing pawn exists.
    if(played.piece==="p"&&Math.abs(Number(played.from[1])-Number(played.to[1]))===2)fen[3]=played.from[0]+String((Number(played.from[1])+Number(played.to[1]))/2);
    frames.push({fen:fen.join(" "),label:`${number}${separator} ${played.san}`});
  }
  return frames;
}

export function mountGameBrowser(target:HTMLElement,client:IntelligenceClient,runId:string,expected:{revision:number;input_id:string;input_revision:number},onBack:()=>void,onError:(error:unknown)=>void,options:{backLabel?:string}={}){
  let active=true,requestGeneration=0,board:Api|undefined;
  const back=()=>{board?.destroy();onBack();};
  function detail(game:Game,selections:z.infer<typeof pageSchema>["selections"]){
    try{
      const frames=gameFrames(game.starting_fen,game.moves);
      board?.destroy();target.replaceChildren(button("Back to selected games",()=>{void load();}));
      const section=node("section");section.className="detail";const boardPanel=node("div");const boardElement=node("div");boardElement.className="board";boardElement.setAttribute("aria-label","Game chessboard");boardPanel.append(boardElement);
      const information=node("div");information.append(node("h2",`${game.provider??"Source"} game ${game.provider_game_id??game.game_id}`),node("p",`${game.played_at??"Time unknown"} · ${game.learner_color}`));
      const source=gameSourceUrl(game.provider,game.game_url);if(source){const link=node("a","Open original game");link.href=source;link.target="_blank";link.rel="noopener noreferrer";information.append(link);}
      const label=node("label","Game position");const position=node("select");position.setAttribute("aria-label","Game position");frames.forEach((frame,ply)=>{const option=node("option",`${ply}: ${frame.label}`);option.value=String(ply);position.append(option);});label.append(position);information.append(label);
      const observation=node("div");observation.setAttribute("aria-label","Accepted comparison");information.append(node("p",comparisonChronologyNotice),observation,node("p","These are frozen input observations. Unknown authority is not a repertoire miss or permission to create a card."));
      let selectedPly=0;
      function show(ply:number){
        selectedPly=Math.max(0,Math.min(ply,frames.length-1));position.value=String(selectedPly);board!.set({fen:frames[selectedPly].fen});
        observation.replaceChildren(node("p",`Actual move: ${game.moves[selectedPly]??"game ended"}`),node("p",`FEN: ${frames[selectedPly].fen}`));
        const contexts=game.contexts.filter(context=>context.ply===selectedPly);
        if(!contexts.length)observation.append(node("p","No selected-policy comparison at this position."));
        for(const context of contexts){const selection=selections.find(item=>item.policy_id===context.policy_id&&item.repertoire_id===context.repertoire_id);observation.append(node("p",`Policy ${selection?.repertoire_title??context.policy_id}: ${context.status} · ${context.reason.replaceAll("_"," ")} · source coverage ${context.coverage}${context.after_divergence===true?" · after divergence; consult the finding report for verified conclusions":""}`));if(context.expected_moves.length)observation.append(node("p",`Accepted moves: ${context.expected_moves.join(", ")}`));}
      }
      position.onchange=()=>show(Number(position.value));
      const actions=node("div");actions.className="board-actions";actions.append(button("Previous position",()=>show(selectedPly-1)),button("Next position",()=>show(selectedPly+1)),button("Flip board",()=>board!.toggleOrientation()));boardPanel.append(actions);
      section.append(boardPanel,information);target.append(section);board=Chessground(boardElement,{fen:frames[0].fen,orientation:game.learner_color,viewOnly:true,coordinates:true,animation:{enabled:false}});show(0);
    }catch(error){onError(error);}
  }
  async function load(after?:string){
    const generation=++requestGeneration;board?.destroy();board=undefined;target.replaceChildren(button(options.backLabel??"Back to findings",back),node("h2","Selected games"));
    try{
      const query=after?`?after=${encodeURIComponent(after)}`:"";
      const page=await client.request(`/v1/analysis/runs/${encodeURIComponent(runId)}/games${query}`,pageSchema);
      if(!active||generation!==requestGeneration)return;
      if(page.run_id!==runId||page.revision!==expected.revision||page.input_id!==expected.input_id||page.input_revision!==expected.input_revision)throw new Error("Game scope identity mismatch");
      target.append(node("p",`${page.game_count} accepted games · run revision ${page.revision} · input revision ${page.input_revision}. Optional engine progress does not change these games.`));
      const list=node("section");for(const game of page.items){const row=node("article");row.append(node("p",`${game.provider??"Source"} · ${game.played_at??"Time unknown"} · ${game.learner_color} · ${game.moves.length} plies`),button(`Inspect game ${game.provider_game_id??game.game_id}`,()=>detail(game,page.selections)));list.append(row);}target.append(list);
      if(page.next_after)target.append(button("More games",()=>{void load(page.next_after!);}));
    }catch(error){if(active&&generation===requestGeneration)onError(error);}
  }
  void load();return ()=>{active=false;requestGeneration++;board?.destroy();};
}
