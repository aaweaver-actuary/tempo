import { DEFAULT_BOARD_APPEARANCE, type BoardAppearance } from "@tempo/board-ui";
import { mountBoard } from "./board.js";
import { Chess } from "chess.js";
import type { GamesReader, Game, GamePage } from './games-reader.js';
import { gameSourceUrl } from "./game-source.js";

export const comparisonChronologyNotice="Repertoire comparisons use the exported snapshot. Repertoire availability and prior study at game time are unverified.";

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

export function mountGameBrowser(target:HTMLElement,reader:GamesReader,runId:string,expected:{revision:number;input_id:string;input_revision:number},onBack:()=>void,onError:(error:unknown)=>void,options:{backLabel?:string;appearance?:BoardAppearance}={}){
  let active=true,requestGeneration=0,board:ReturnType<typeof mountBoard>|undefined;
  const back=()=>{board?.destroy();onBack();};
  function detail(game:Game,selections:GamePage['selections']){
    try{
      const frames=gameFrames(game.starting_fen,game.moves);
      board?.destroy();target.replaceChildren(button("Back to selected games",()=>{void load();}));
      const section=node("section");section.className="detail";const boardPanel=node("div");const boardElement=node("div");boardElement.className="board";boardElement.setAttribute("aria-label","Game chessboard");boardPanel.append(boardElement);
      const information=node("div");information.append(node("h2",`${game.provider??"Source"} game ${game.provider_game_id??game.game_id}`),node("p",`${game.played_at??"Time unknown"} · ${game.learner_color}`));
      const source=gameSourceUrl(game.provider,game.game_url);if(source){const link=node("a","Open original game");link.href=source;link.target="_blank";link.rel="noopener noreferrer";information.append(link);}
      const label=node("label","Game position");const position=node("select");position.setAttribute("aria-label","Game position");frames.forEach((frame,ply)=>{const option=node("option",`${ply}: ${frame.label}`);option.value=String(ply);position.append(option);});label.append(position);information.append(label);
      const observation=node("div");observation.setAttribute("aria-label","Accepted comparison");information.append(node("p",comparisonChronologyNotice),observation,node("p","These are frozen input observations. Unknown authority is not a repertoire miss or permission to create a card."));
      let selectedPly=0,orientation=game.learner_color;
      function show(ply:number){
        selectedPly=Math.max(0,Math.min(ply,frames.length-1));position.value=String(selectedPly);board!.update({fen:frames[selectedPly].fen});
        observation.replaceChildren(node("p",`Actual move: ${game.moves[selectedPly]??"game ended"}`),node("p",`FEN: ${frames[selectedPly].fen}`));
        const contexts=game.contexts.filter(context=>context.ply===selectedPly);
        if(!contexts.length)observation.append(node("p","No selected-policy comparison at this position."));
        for(const context of contexts){const selection=selections.find(item=>item.policy_id===context.policy_id&&item.repertoire_id===context.repertoire_id);observation.append(node("p",`Policy ${selection?.repertoire_title??context.policy_id}: ${context.status} · ${context.reason.replaceAll("_"," ")} · source coverage ${context.coverage}${context.after_divergence===true?" · after divergence; consult the finding report for verified conclusions":""}`));if(context.expected_moves.length)observation.append(node("p",`Accepted moves: ${context.expected_moves.join(", ")}`));}
      }
      position.onchange=()=>show(Number(position.value));
      const actions=node("div");actions.className="board-actions";actions.append(button("Previous position",()=>show(selectedPly-1)),button("Next position",()=>show(selectedPly+1)),button("Flip board",()=>{orientation=orientation==="white"?"black":"white";board!.update({orientation});}));boardPanel.append(actions);
      section.append(boardPanel,information);target.append(section);board=mountBoard(boardElement,{fen:frames[0].fen,orientation,identity:game.game_id,appearance:options.appearance??DEFAULT_BOARD_APPEARANCE,label:"Game board surface"});show(0);
    }catch(error){onError(error);}
  }
  async function load(after?:string){
    const generation=++requestGeneration;board?.destroy();board=undefined;target.replaceChildren(button(options.backLabel??"Back to findings",back),node("h2","Selected games"));
    try{
      const page=await reader.readGames({run_id:runId,...expected},after);
      if(!active||generation!==requestGeneration)return;
      if(page.run_id!==runId||page.revision!==expected.revision||page.input_id!==expected.input_id||page.input_revision!==expected.input_revision)throw new Error("Game scope identity mismatch");
      target.append(node("p",`${page.game_count} accepted games · run revision ${page.revision} · input revision ${page.input_revision}. Optional engine progress does not change these games.`));
      const list=node("section");for(const game of page.items){const row=node("article");row.append(node("p",`${game.provider??"Source"} · ${game.played_at??"Time unknown"} · ${game.learner_color} · ${game.moves.length} plies`),button(`Inspect game ${game.provider_game_id??game.game_id}`,()=>detail(game,page.selections)));list.append(row);}target.append(list);
      if(page.next_after)target.append(button("More games",()=>{void load(page.next_after!);}));
    }catch(error){if(active&&generation===requestGeneration)onError(error);}
  }
  void load();return ()=>{active=false;requestGeneration++;board?.destroy();};
}
