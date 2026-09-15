import { API_URL, STANDARD_FEN } from "../const";
import { Square, Chess } from "chess.js";
import { useState, useEffect, useCallback } from "react";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { generateLegalEndgameFen } from "../lib/endgame-generator";
import { endgameTemplates } from "../samples";
import { probeTablebase, tablebaseCategoryForWhite } from "../utils/tablebase";
import { usesLocalApi } from "../utils/local";

export default function EndgamesView({ theme, pieceSet, onQueueChanged }: { theme: BoardTheme; pieceSet: PieceSet; onQueueChanged: () => void }) {
  const [selected, setSelected] = useState(1);
  const [classification, setClassification] = useState<'win' | 'draw' | null>(null);
  const [target, setTarget] = useState<'win' | 'draw'>('win');
  const [status, setStatus] = useState('Finding a legal tablebase position…');
  const [fen, setFen] = useState(STANDARD_FEN);
  const [busy,setBusy]=useState(true);
  const [userMoves,setUserMoves]=useState(0);
  const [complete,setComplete]=useState(false);
  const [admitted,setAdmitted]=useState<Record<number,{templateId:string;cardId:string}>>({});

  useEffect(()=>{
    if(!usesLocalApi())return;
    fetch(`${API_URL}/api/endgames/templates`)
      .then((response)=>response.ok?response.json():Promise.reject())
      .then((value)=>{
        const data = value as { templates: Array<{ id: string; card_id: string; white_material: string; black_material: string; }> };
        const next:Record<number,{templateId:string;cardId:string}>={};
        endgameTemplates.forEach((template,index)=>{
          const found=data.templates.find((item)=>item.white_material===template.white&&item.black_material===template.black);
          if(found) next[index]={templateId:found.id,cardId:found.card_id};
        });
        setAdmitted(next);
      })
      .catch(()=>undefined);
  },[]);

  async function admitTemplate(){
    if(!usesLocalApi())return;
    const template=endgameTemplates[selected];
    const response=await fetch(`${API_URL}/api/endgames/templates`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:template.name,white_material:template.white,black_material:template.black,trained_color:'white',goal_mix:'both'})});
    if(!response.ok){setStatus('Could not add this material set to training.');return;}
    const data=await response.json() as {id:string;card_id:string};
    setAdmitted((current)=>({...current,[selected]:{templateId:data.id,cardId:data.card_id}}));
    setStatus('Added to your daily training.'); onQueueChanged();
  }

  function recordEndgame(outcome:'correct'|'again'){
    const item=admitted[selected];
    if(!item||!usesLocalApi())return;
    void fetch(`${API_URL}/api/cards/${item.cardId}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outcome,guided:outcome==='again'})}).then(()=>onQueueChanged()).catch(()=>undefined);
  }

  const newPosition = useCallback(async (index = selected) => {
    setBusy(true); setClassification(null); setComplete(false); setUserMoves(0); setStatus('Finding a legal tablebase position…');
    for(let attempt=0;attempt<24;attempt+=1){
      const candidate=generateLegalEndgameFen(endgameTemplates[index]);
      try {
        const result=await probeTablebase(candidate);
        const category=tablebaseCategoryForWhite(candidate,result.category);
        if(category==='loss')continue;
        setFen(candidate); setTarget(category); setStatus('Classify the position before playing.'); setBusy(false); return;
      } catch { /* Try another legal position before reporting the service unavailable. */ }
    }
    setStatus('The tablebase could not provide a position. Try again when online.'); setBusy(false);
  },[selected]);

  useEffect(()=>{
    const timer = window.setTimeout(() => {
      void newPosition(selected);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [newPosition, selected]);

  function classify(value: 'win' | 'draw') {
    setClassification(value);
    setStatus(value === target ? `Correct · now ${target === 'win' ? 'convert the win' : 'hold the draw'}.` : `This position is a ${target}. Try the classification again.`);
  }

  async function play(from: Square, to: Square) {
    if (classification !== target || busy || complete) return;
    const board = new Chess(fen);
    try { board.move({ from, to, promotion: 'q' }); } catch { return; }
    setFen(board.fen()); setBusy(true);
    if(board.isCheckmate()){setComplete(true);setStatus('Converted · template review complete.');setBusy(false);recordEndgame('correct');return;}
    try {
      const afterUser=await probeTablebase(board.fen());
      const userCategory=tablebaseCategoryForWhite(board.fen(),afterUser.category);
      if((target==='win'&&userCategory!=='win')||(target==='draw'&&userCategory==='loss')){setComplete(true);setStatus(`Failed · the position is now a ${userCategory}.`);setBusy(false);recordEndgame('again');return;}
      const defense=afterUser.moves?.[0]?.uci;
      if(defense){board.move({from:defense.slice(0,2) as Square,to:defense.slice(2,4) as Square,promotion:defense[4]});setFen(board.fen());}
      const count=userMoves+1;setUserMoves(count);
      if(board.isGameOver()){const success=target==='draw'&&!board.isCheckmate();setComplete(true);setStatus(success?'Draw secured · template review complete.':'The defender held the position.');recordEndgame(success?'correct':'again');}
      else if(target==='draw'&&count>=20){setComplete(true);setStatus('Draw held for 20 moves · template review complete.');recordEndgame('correct');}
      else setStatus(`${target==='win'?'Winning':'Drawing'} status preserved · tablebase defense played.`);
    } catch { setStatus('The tablebase response failed. Your move remains on the board.'); }
    setBusy(false);
  }

  return (
    <section className="endgames-page">
      <div className="workspace-title"><div><h1>Endgames</h1><span>Exact seven-piece practice</span></div><button className="primary-button" disabled={Boolean(admitted[selected])||!usesLocalApi()} onClick={()=>void admitTemplate()}>{admitted[selected]?'✓ In daily training':'＋ Add to daily training'}</button></div>
      <div className="endgame-workspace">
        <aside className="template-list">{endgameTemplates.map((template, index) => <button className={selected === index ? 'active' : ''} key={template.name} onClick={() => setSelected(index)}><strong>{template.name}</strong><small>{template.white} vs {template.black} · White</small></button>)}</aside>
        <div className="board-column centered-board"><Chessboard fen={fen} locked={busy || classification !== target || complete} showHint={false} theme={theme} pieceSet={pieceSet} onMove={(from,to)=>void play(from,to)}/><div className="board-tools"><button onClick={() => void newPosition()}>⤨ <span>New position</span></button><button>⚙ <span>Edit material</span></button></div></div>
        <aside className="study-panel endgame-study"><span className="pill">Material template</span><h2>{endgameTemplates[selected].name}</h2><p>White to move · exact tablebase</p><div className="classification"><button className={classification === 'win' ? 'active' : ''} disabled={busy} onClick={() => classify('win')}>Win</button><button className={classification === 'draw' ? 'active' : ''} disabled={busy} onClick={() => classify('draw')}>Draw</button></div><div className={`feedback ${complete&&status.startsWith('Failed')?'wrong':'ready'}`}><span className="feedback-icon">{busy?'…':complete?'✓':'●'}</span><div><strong>{classification === target ? 'Play the position' : 'Win or draw?'}</strong><p>{status}</p></div></div><small>{target==='draw'?`${userMoves} / 20 accurate user moves`:'Checkmate completes the card.'}</small></aside>
      </div>
    </section>
  );
}