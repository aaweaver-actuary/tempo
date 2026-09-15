import hashlib, io, json, uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import chess, chess.pgn, httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, initialize
from .models import AccountSettings, BranchRequest, CardRevisionRequest, EndgameProbeRequest, EndgameTemplateRequest, GameAnalysisRequest, GameSyncRequest, ImportResult, RepertoireRenameRequest, ReviewRequest, Settings, TacticAttemptRequest
from .services.analysis import AnalysisCapabilities
from .services.cards import card_id
from .services.pgn import parse_pgn, prefix_through_user_moves
from .services.scheduler import schedule_review, unlock_ready
from .services.endgames import category_for_player, generate_position, normalized_material
from .services.game_analysis import classify_swings
from .services.puzzles import load_packaged_decks, validate_puzzle_record

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize(); yield

app=FastAPI(title="Tempo local API",version="0.2.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",allow_methods=["*"],allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status":"ok","storage":"local-sqlite","scheduler":"FSRS 6"}

@app.get("/api/analysis/capabilities")
def capabilities(): return AnalysisCapabilities()

@app.get("/api/settings",response_model=Settings)
def get_settings():
    with connection() as db: row=db.execute("SELECT initial_depth,timezone,new_cards_per_day,lichess_username,chesscom_username,auto_sync_minutes,engine_line_window_cp,major_mistake_cp,light_first_interval_days,draw_hold_user_moves FROM settings WHERE id=1").fetchone()
    return Settings(**dict(row))

@app.put("/api/settings",response_model=Settings)
def put_settings(s: Settings):
    with connection() as db: db.execute("UPDATE settings SET initial_depth=?,timezone=?,new_cards_per_day=?,lichess_username=?,chesscom_username=?,auto_sync_minutes=?,engine_line_window_cp=?,major_mistake_cp=?,light_first_interval_days=?,draw_hold_user_moves=? WHERE id=1",(s.initial_depth,s.timezone,s.new_cards_per_day,s.lichess_username,s.chesscom_username,s.auto_sync_minutes,s.engine_line_window_cp,s.major_mistake_cp,s.light_first_interval_days,s.draw_hold_user_moves))
    return s

def reconcile_unseen_queue(db, day, limit):
    """Trim legacy queues that eagerly admitted every unseen card."""
    rows = db.execute("""
        SELECT q.id,q.card_id FROM daily_queue q
        JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' AND c.state='new'
          AND c.introduced_at IS NULL
          AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
        ORDER BY q.position,q.id
    """, (day,)).fetchall()
    introduced=db.execute("SELECT COUNT(*) FROM cards WHERE introduced_at=?",(day,)).fetchone()[0]
    allowed=max(0,limit-introduced)
    for row in rows[:allowed]:
        db.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",(day,row["card_id"]))
    for row in rows[allowed:]:
        db.execute("DELETE FROM daily_queue WHERE id=?", (row["id"],))

def seed_queue(db,day):
    limit=db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[0]
    reconcile_unseen_queue(db,day,limit)
    maximum=db.execute("SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?",(day,)).fetchone()[0]
    rows=db.execute("SELECT id FROM cards WHERE due_date<=? AND state IN ('learning','mature') AND archived=0 AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id",(day,day)).fetchall()
    for offset,row in enumerate(rows,1): db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",(day,row[0],maximum+offset))
    maximum+=len(rows)
    introduced=db.execute("SELECT COUNT(*) FROM cards WHERE introduced_at=?",(day,)).fetchone()[0]
    remaining=max(0,limit-introduced)
    new_rows=db.execute("SELECT id FROM cards WHERE due_date<=? AND state='new' AND introduced_at IS NULL AND archived=0 ORDER BY due_date,id LIMIT ?",(day,remaining)).fetchall()
    for offset,row in enumerate(new_rows,1):
        db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",(day,row[0],maximum+offset))
        db.execute("UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",(day,row[0]))

@app.get("/api/queue/today")
def queue_today():
    day=date.today().isoformat()
    with connection() as db:
        seed_queue(db,day)
        rows=db.execute("""SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,c.*,
                                  r.name repertoire_name,r.source_name repertoire_source,r.is_main,
                                  (SELECT l.trained_color FROM repertoire_lines l WHERE l.repertoire_id=r.id ORDER BY l.created_at LIMIT 1) trained_color
                           FROM daily_queue q JOIN cards c ON c.id=q.card_id
                           JOIN repertoires r ON r.id=COALESCE(
                               (SELECT rc.repertoire_id FROM repertoire_cards rc JOIN repertoires linked ON linked.id=rc.repertoire_id
                                WHERE rc.card_id=c.id ORDER BY linked.is_main DESC,linked.created_at DESC LIMIT 1),
                               c.repertoire_id)
                           WHERE q.queue_date=? AND q.status='queued'
                           ORDER BY q.position,q.id""",(day,)).fetchall()
    cards=[{**dict(r),"moves":json.loads(r["moves_json"])} for r in rows]
    for card in cards: card.pop("moves_json",None)
    return {"local_date":day,"cards":cards,"count":len(cards)}

@app.post("/api/imports/pgn",response_model=ImportResult)
async def import_pgn(file:UploadFile=File(...),trained_color:str=Form("white"),initial_depth:int|None=Form(None)):
    if not file.filename or not file.filename.lower().endswith('.pgn'): raise HTTPException(400,"Choose a .pgn file")
    if trained_color not in {"white","black"}: raise HTTPException(400,"trained_color must be white or black")
    games,lines=parse_pgn((await file.read()).decode("utf-8-sig"))
    if not lines: raise HTTPException(422,"No playable lines were found")
    seen,created=set(),0; now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        saved_depth=db.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]
        depth=max(2,min(20,initial_depth if initial_depth is not None else saved_depth))
        existing_repertoire=db.execute("SELECT id FROM repertoires WHERE source_name=? AND id NOT IN ('__tactics__','__endgames__') ORDER BY created_at DESC LIMIT 1",(file.filename,)).fetchone()
        rid=existing_repertoire["id"] if existing_repertoire else str(uuid.uuid4())
        db.execute("UPDATE repertoires SET is_main=0 WHERE id NOT IN ('__tactics__','__endgames__')")
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET is_main=1,source_name=excluded.source_name",(rid,file.filename.rsplit('.',1)[0],file.filename,now))
        for line in lines:
            moves_json=json.dumps(line.moves)
            existing_line=db.execute("SELECT id FROM repertoire_lines WHERE repertoire_id=? AND start_fen=? AND moves_json=?",(rid,line.starting_fen,moves_json)).fetchone()
            line_id=existing_line["id"] if existing_line else hashlib.sha256(f"{rid}\0{card_id(line.starting_fen,line.moves)}".encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",(line_id,rid,file.filename,trained_color,line.starting_fen,json.dumps(line.moves),now))
            moves=prefix_through_user_moves(line.starting_fen,line.moves,trained_color,depth)
            if not moves: continue
            cid=card_id(line.starting_fen,moves)
            if cid in seen: continue
            seen.add(cid); created+=db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)",(cid,rid,line.starting_fen,json.dumps(moves),date.today().isoformat())).rowcount
            db.execute("INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",(rid,cid))
        seed_queue(db,date.today().isoformat())
        admitted=db.execute("""SELECT COUNT(DISTINCT q.card_id) FROM daily_queue q JOIN repertoire_cards rc ON rc.card_id=q.card_id
                               WHERE q.queue_date=? AND q.status='queued' AND rc.repertoire_id=?""",(date.today().isoformat(),rid)).fetchone()[0]
    return ImportResult(repertoire_id=rid,source_name=file.filename,games_found=games,unique_lines=len(seen),cards_created=created,duplicates_merged=max(0,len(lines)-created),cards_admitted_today=admitted)

@app.get("/api/repertoires")
def list_repertoires():
    with connection() as db:
        seed_queue(db,date.today().isoformat())
        rows=db.execute("""
            SELECT r.id,r.name,r.source_name,r.created_at,r.is_main,
                   COUNT(DISTINCT l.id) AS line_count,
                   COUNT(DISTINCT c.id) AS card_count,
                   (SELECT l2.trained_color FROM repertoire_lines l2 WHERE l2.repertoire_id=r.id ORDER BY l2.created_at LIMIT 1) AS trained_color,
                   COUNT(DISTINCT CASE WHEN q.queue_date=? AND q.status='queued' THEN q.card_id END) AS due_count
            FROM repertoires r
            LEFT JOIN repertoire_lines l ON l.repertoire_id=r.id
            LEFT JOIN repertoire_cards rc ON rc.repertoire_id=r.id
            LEFT JOIN cards c ON c.id=rc.card_id
            LEFT JOIN daily_queue q ON q.card_id=c.id
            WHERE r.id NOT IN ('__tactics__','__endgames__')
            GROUP BY r.id,r.name,r.source_name,r.created_at
            ORDER BY r.created_at DESC
        """,(date.today().isoformat(),)).fetchall()
    return {"repertoires":[dict(row) for row in rows]}

@app.get("/api/repertoire/lines")
def repertoire_lines():
    with connection() as db:
        rows=db.execute("""SELECT l.id,l.repertoire_id,l.name,l.trained_color,l.start_fen,l.moves_json,
                                  r.name repertoire_name,r.is_main
                           FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id
                           WHERE r.id NOT IN ('__tactics__','__endgames__') ORDER BY r.created_at,l.created_at""").fetchall()
    return {"lines":[{**dict(row),"moves":json.loads(row["moves_json"])} for row in rows]}

@app.put("/api/repertoires/{identifier}/main")
def make_main_repertoire(identifier:str):
    with connection() as db:
        if not db.execute("SELECT 1 FROM repertoires WHERE id=? AND id NOT IN ('__tactics__','__endgames__')",(identifier,)).fetchone():
            raise HTTPException(404,"Repertoire not found")
        db.execute("UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__')",(identifier,))
    return {"id":identifier,"is_main":True}

@app.delete("/api/repertoires/{identifier}")
def delete_repertoire(identifier:str):
    if identifier in {"__tactics__","__endgames__"}: raise HTTPException(400,"This system repertoire cannot be deleted")
    with connection() as db:
        if not db.execute("SELECT 1 FROM repertoires WHERE id=?",(identifier,)).fetchone(): raise HTTPException(404,"Repertoire not found")
        shared=db.execute("""SELECT c.id,(SELECT rc2.repertoire_id FROM repertoire_cards rc2
                                          WHERE rc2.card_id=c.id AND rc2.repertoire_id!=? LIMIT 1) replacement
                             FROM cards c WHERE c.repertoire_id=? AND EXISTS(
                                 SELECT 1 FROM repertoire_cards rc3 WHERE rc3.card_id=c.id AND rc3.repertoire_id!=?)""",(identifier,identifier,identifier)).fetchall()
        for card in shared: db.execute("UPDATE cards SET repertoire_id=? WHERE id=?",(card["replacement"],card["id"]))
        db.execute("DELETE FROM repertoires WHERE id=?",(identifier,))
        replacement=db.execute("SELECT id FROM repertoires WHERE id NOT IN ('__tactics__','__endgames__') ORDER BY created_at DESC LIMIT 1").fetchone()
        if replacement: db.execute("UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END WHERE id NOT IN ('__tactics__','__endgames__')",(replacement[0],))
    return {"deleted":True,"id":identifier}

def requeue(db,day,cid,after,attempt):
    cycle=db.execute("SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",(day,cid)).fetchone()[0]
    if after is None: position=db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",(day,)).fetchone()[0]
    else:
        row=db.execute("SELECT position FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id LIMIT 1 OFFSET ?",(day,max(0,after-1))).fetchone()
        position=row[0] if row else db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",(day,)).fetchone()[0]
        db.execute("UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",(day,position))
    db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) VALUES(?,?,?,?,?)",(day,cid,cycle,position,attempt))

@app.post("/api/cards/{identifier}/review")
def review(identifier:str,request:ReviewRequest):
    now=datetime.now(timezone.utc); day=date.today().isoformat()
    with connection() as db:
        card=db.execute("SELECT interval_days,fsrs_card_json,first_correct_at,reinforcement_pending,scheduling_mode,hard_correct_streak,recent_attempts_json FROM cards WHERE id=? AND archived=0",(identifier,)).fetchone()
        if not card: raise HTTPException(404,"Card not found")
        settings=get_settings()
        s=schedule_review(request.outcome,interval_days=card[0],fsrs_card_json=card[1],first_correct_at=card[2],reinforcement_pending=bool(card[3]),scheduling_mode=card[4],hard_correct_streak=card[5],recent_attempts=json.loads(card[6] or "[]"),light_first_interval_days=settings.light_first_interval_days,reviewed_at=now)
        if request.queue_entry_id: db.execute("UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=? AND card_id=?",("guided" if request.guided else "clean",request.queue_entry_id,identifier))
        else: db.execute("UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position LIMIT 1)",(day,identifier))
        db.execute("INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval) VALUES(?,?,?,?,?,?,?)",(identifier,request.outcome,s.internal_rating,int(request.guided),now.isoformat(),card[0],s.interval_days))
        days=db.execute("SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct'",(identifier,)).fetchone()[0]
        recent=[r[0] for r in db.execute("SELECT rating FROM reviews WHERE card_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 2",(identifier,))]
        state="mature" if unlock_ready(s.stability,days,recent) else "learning"
        db.execute("UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,reinforcement_pending=?,stability=?,guided_review=?,state=?,scheduling_mode=?,hard_correct_streak=?,recent_attempts_json=? WHERE id=?",(s.due_date.isoformat(),s.interval_days,s.fsrs_card_json,s.first_correct_at,int(s.reinforcement_pending),s.stability,int(request.guided),state,s.scheduling_mode,s.hard_correct_streak,json.dumps(s.recent_attempts),identifier))
        if s.requeue_today: requeue(db,day,identifier,s.requeue_after_cards,"guided" if request.outcome=="again" else "reinforcement")
        if state=="mature": db.execute("UPDATE cards SET state='new',due_date=? WHERE unlock_after_card_id=? AND state='locked'",(day,identifier))
    return {"card_id":identifier,"next_due":s.due_date,"interval_days":s.interval_days,"state":state,"requeue_today":s.requeue_today,"requeue_after_cards":s.requeue_after_cards,"stability":s.stability,"scheduling_mode":s.scheduling_mode,"hard_correct_streak":s.hard_correct_streak,"suggest_shorter_prefix":s.suggest_shorter_prefix}

@app.post("/api/repertoire/branches")
def branch(request:BranchRequest):
    board=chess.Board(request.starting_fen); moves=[]
    try:
        for value in request.moves:
            move=chess.Move.from_uci(value.lower())
            if move not in board.legal_moves: raise ValueError
            moves.append(move.uci()); board.push(move)
    except ValueError: raise HTTPException(422,"Branch contains an illegal move")
    lid=card_id(request.starting_fen,moves); now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        duplicate=db.execute("SELECT 1 FROM repertoire_lines WHERE id=?",(lid,)).fetchone() is not None
        db.execute("INSERT OR IGNORE INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",(lid,request.repertoire_id,request.name,request.trained_color,request.starting_fen,json.dumps(moves),now))
    return {"id":lid,"duplicate":duplicate,"moves":moves}

def validated_line(starting_fen:str, values:list[str]):
    try: board=chess.Board(starting_fen)
    except ValueError as error: raise HTTPException(422,f"Invalid FEN: {error}")
    moves=[]
    for value in values:
        try: move=chess.Move.from_uci(value.lower())
        except ValueError: raise HTTPException(422,f"Invalid UCI move: {value}")
        if move not in board.legal_moves: raise HTTPException(422,f"Illegal move: {value}")
        moves.append(move.uci()); board.push(move)
    if not moves: raise HTTPException(422,"A card needs at least one move")
    return moves

@app.post("/api/cards/validate")
def validate_card(request:CardRevisionRequest):
    moves=validated_line(request.starting_fen,request.moves)
    return {"valid":True,"canonical_id":card_id(request.starting_fen,moves),"moves":moves}

@app.put("/api/cards/{identifier}")
def revise_card(identifier:str,request:CardRevisionRequest):
    moves=validated_line(request.starting_fen,request.moves); replacement=card_id(request.starting_fen,moves); now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        old=db.execute("SELECT * FROM cards WHERE id=?",(identifier,)).fetchone()
        if not old: raise HTTPException(404,"Card not found")
        existing=db.execute("SELECT * FROM cards WHERE id=?",(replacement,)).fetchone()
        revision=int(old["revision"] or 1)+1
        db.execute("INSERT OR IGNORE INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) VALUES(?,?,?,?,?,?)",(identifier,revision,old["start_fen"],old["moves_json"],request.history_mode,now))
        if replacement==identifier:
            db.execute("UPDATE cards SET start_fen=?,moves_json=?,source_fen=?,revision=? WHERE id=?",(request.starting_fen,json.dumps(moves),request.source_fen,revision,identifier))
        elif existing:
            if request.history_mode=="preserve":
                db.execute("UPDATE reviews SET card_id=? WHERE card_id=?",(replacement,identifier))
            db.execute("UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",(identifier,))
            db.execute("UPDATE cards SET archived=1,superseded_by=? WHERE id=?",(replacement,identifier))
        else:
            fields=dict(old); fields.update({"id":replacement,"start_fen":request.starting_fen,"moves_json":json.dumps(moves),"source_fen":request.source_fen,"revision":revision,"archived":0,"superseded_by":None})
            if request.history_mode=="reset":
                fields.update({"due_date":date.today().isoformat(),"interval_days":0,"repetitions":0,"lapses":0,"fsrs_card_json":None,"first_correct_at":None,"reinforcement_pending":0,"stability":0,"guided_review":0,"state":"new","scheduling_mode":"normal","hard_correct_streak":0,"recent_attempts_json":"[]"})
            columns=[row[1] for row in db.execute("PRAGMA table_info(cards)")]
            db.execute(f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",tuple(fields.get(column) for column in columns))
            if request.history_mode=="preserve": db.execute("UPDATE reviews SET card_id=? WHERE card_id=?",(replacement,identifier))
            db.execute("UPDATE daily_queue SET card_id=? WHERE card_id=? AND status='queued'",(replacement,identifier))
            db.execute("UPDATE cards SET archived=1,superseded_by=? WHERE id=?",(replacement,identifier))
    return {"card_id":replacement,"replaced":replacement!=identifier,"history_mode":request.history_mode}

@app.delete("/api/cards/{identifier}")
def archive_card(identifier:str):
    with connection() as db:
        if not db.execute("UPDATE cards SET archived=1 WHERE id=?",(identifier,)).rowcount: raise HTTPException(404,"Card not found")
        db.execute("UPDATE daily_queue SET status='complete' WHERE card_id=? AND status='queued'",(identifier,))
    return {"archived":True}

@app.patch("/api/repertoires/{identifier}")
def rename_repertoire(identifier:str,request:RepertoireRenameRequest):
    with connection() as db:
        if not db.execute("UPDATE repertoires SET name=? WHERE id=?",(request.name.strip(),identifier)).rowcount: raise HTTPException(404,"Repertoire not found")
    return {"id":identifier,"name":request.name.strip()}

def export_repertoires(repertoire_id:str|None=None):
    with connection() as db:
        query="SELECT l.*,r.name repertoire_name FROM repertoire_lines l JOIN repertoires r ON r.id=l.repertoire_id"
        rows=db.execute(query+(" WHERE l.repertoire_id=?" if repertoire_id else "")+(" ORDER BY l.created_at"),((repertoire_id,) if repertoire_id else ())).fetchall()
    stream=io.StringIO()
    for row in rows:
        game=chess.pgn.Game(); game.headers["Event"]=row["repertoire_name"]; game.headers["SetUp"]="1"; game.headers["FEN"]=row["start_fen"]
        board=game.board(); node=game
        for value in json.loads(row["moves_json"]):
            move=chess.Move.from_uci(value); node=node.add_main_variation(move); board.push(move)
        print(game,file=stream,end="\n\n")
    return PlainTextResponse(stream.getvalue(),media_type="application/x-chess-pgn",headers={"Content-Disposition":"attachment; filename=tempo-repertoire.pgn"})

@app.get("/api/repertoires/{identifier}/export.pgn")
def export_one(identifier:str): return export_repertoires(identifier)

@app.get("/api/repertoires/export.pgn")
def export_all(): return export_repertoires()

@app.get("/api/explorer/{database_name}")
async def explorer(database_name:str,fen:str,speeds:str="blitz,rapid,classical",ratings:str="1600,1800,2000,2200,2500",since:int|None=None,until:int|None=None,authorization:str|None=Header(None)):
    if database_name not in {"lichess","masters"}: raise HTTPException(404,"Unknown Explorer database")
    params={"variant":"standard","fen":fen}
    if database_name=="lichess": params.update({"speeds":speeds,"ratings":ratings})
    if since: params["since"]=since
    if until: params["until"]=until
    async with httpx.AsyncClient(timeout=15) as client: response=await client.get(f"https://explorer.lichess.org/{database_name}",params=params,headers={"Authorization":authorization} if authorization else {})
    if response.status_code==429: raise HTTPException(429,"Explorer rate limit reached")
    if not response.is_success: raise HTTPException(response.status_code,"Explorer request failed")
    return response.json()

TACTIC_MOTIFS=[
    ("hangingPiece","Hanging pieces","♟"),("fork","Forks","♘"),("pin","Pins","⌖"),("skewer","Skewers","⇥"),("discoveredAttack","Discoveries","✦"),
    ("mateIn1","Mate in 1","#1"),("mateIn2","Mate in 2","#2"),("mateIn3","Mate in 3","#3"),("mateIn4Plus","Mate in 4+","#4"),
    ("calculation2","2-move calculation","2×"),("calculation3","3-move calculation","3×"),("calculation4","4-move calculation","4×"),("trappedPiece","Trapped pieces","▣"),
]

@app.get("/api/tactics/catalog")
def tactics_catalog():
    with connection() as db:
        progress={row[0]:row[1] for row in db.execute("SELECT deck_id,COUNT(*) FROM tactic_progress WHERE clean_pass_at IS NOT NULL GROUP BY deck_id")}
    decks=[]
    for motif,name,icon in TACTIC_MOTIFS:
        stages=[]; previous_complete=True
        for tier,label,range_text,size in (("easy","Easy","700–1100",100),("medium","Medium","1101–1500",100),("hard","Hard","1501–2000",100),("focused","Focused","1250–2000",250)):
            deck_id=f"{motif}-{tier}"; clean=progress.get(deck_id,0); unlocked=previous_complete
            stages.append({"id":deck_id,"label":label,"range":range_text,"size":size,"clean":clean,"unlocked":unlocked})
            previous_complete=previous_complete and clean>=size
        decks.append({"id":motif,"name":name,"icon":icon,"stages":stages})
    return {"default":"hangingPiece","motifs":decks}

@app.post("/api/tactics/attempt")
def tactic_attempt(request:TacticAttemptRequest):
    record={"FEN":request.source_fen,"Moves":" ".join(request.moves)}
    training_fen,solution=validate_puzzle_record(record); now=datetime.now(timezone.utc); cid=card_id(training_fen,solution)
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','Lichess puzzle database',?)",(now.isoformat(),))
        clean_at=now.isoformat() if request.correct and request.clean else None
        db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) VALUES(?,?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET clean_pass_at=COALESCE(tactic_progress.clean_pass_at,excluded.clean_pass_at),card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at),admission_mode=excluded.admission_mode",(request.puzzle_id,request.deck_id,cid,clean_at,now.isoformat(),"light" if request.correct and request.clean else "normal"))
        db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,scheduling_mode,source_ref,source_fen,state) VALUES(?, '__tactics__','checkpoint',?,?,?,?,?,?,?,'learning')",(cid,training_fen,json.dumps(solution),(now.date()+timedelta(days=7) if request.correct and request.clean else now.date()).isoformat(),"tactic","light" if request.correct and request.clean else "normal",request.puzzle_id,request.source_fen))
        db.execute("UPDATE cards SET state='learning' WHERE id=?",(cid,))
        if not request.correct:
            requeue(db,now.date().isoformat(),cid,4,"guided")
    return {"card_id":cid,"mode":"light" if request.correct and request.clean else "normal","next_due":(now.date()+timedelta(days=7) if request.correct and request.clean else now.date())}

async def tablebase(fen:str):
    key=" ".join(fen.split()[:4])
    with connection() as db:
        row=db.execute("SELECT response_json FROM tablebase_cache WHERE fen_key=?",(key,)).fetchone()
    if row: return json.loads(row[0])
    async with httpx.AsyncClient(timeout=15) as client: response=await client.get("https://tablebase.lichess.ovh/standard",params={"fen":fen})
    if response.status_code==429: raise HTTPException(429,"Tablebase rate limit reached")
    if not response.is_success: raise HTTPException(response.status_code,"Position is outside complete tablebase coverage")
    data=response.json()
    with connection() as db: db.execute("INSERT OR REPLACE INTO tablebase_cache VALUES(?,?,?)",(key,json.dumps(data),datetime.now(timezone.utc).isoformat()))
    return data

@app.post("/api/endgames/probe")
async def probe_endgame(request:EndgameProbeRequest): return await tablebase(request.fen)

@app.get("/api/endgames/templates")
def list_endgames():
    with connection() as db: rows=db.execute("SELECT * FROM endgame_templates WHERE enabled=1 ORDER BY created_at").fetchall()
    return {"templates":[dict(row) for row in rows]}

@app.post("/api/endgames/templates")
def create_endgame(request:EndgameTemplateRequest):
    try: white=normalized_material(request.white_material); black=normalized_material(request.black_material); sample=generate_position(white,black,request.trained_color)
    except ValueError as error: raise HTTPException(422,str(error))
    cid=card_id(chess.STARTING_FEN,[f"template:{white}:{black}:{request.trained_color}:{request.goal_mix}"]); now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__endgames__','Endgames','Generated material templates',?)",(now,))
        existing=db.execute("SELECT id,card_id FROM endgame_templates WHERE white_material=? AND black_material=? AND trained_color=? AND goal_mix=? AND enabled=1",(white,black,request.trained_color,request.goal_mix)).fetchone()
        if existing:
            return {"id":existing["id"],"card_id":existing["card_id"],"sample_fen":sample,"already_exists":True}
        identifier=str(uuid.uuid4())
        db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES(?,'__endgames__','checkpoint',?,'[]',?,'endgame')",(cid,sample,date.today().isoformat()))
        db.execute("INSERT INTO endgame_templates VALUES(?,?,?,?,?,?,?,?,?)",(identifier,cid,request.name,white,black,request.trained_color,request.goal_mix,1,now))
    return {"id":identifier,"card_id":cid,"sample_fen":sample,"already_exists":False}

@app.post("/api/endgames/templates/{identifier}/attempt")
async def create_endgame_attempt(identifier:str):
    with connection() as db: template=db.execute("SELECT * FROM endgame_templates WHERE id=? AND enabled=1",(identifier,)).fetchone()
    if not template: raise HTTPException(404,"Endgame template not found")
    for _ in range(80):
        fen=generate_position(template["white_material"],template["black_material"],template["trained_color"])
        data=await tablebase(fen); target=category_for_player(data.get("category","unknown"))
        if target!="loss" and (template["goal_mix"]=="both" or target==template["goal_mix"]): break
    else: raise HTTPException(422,"Could not find a supported win/draw position")
    attempt=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat()
    with connection() as db: db.execute("INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES(?,?,?,?,?)",(attempt,identifier,fen,target,now))
    return {"id":attempt,"fen":fen,"target":target,"moves":data.get("moves",[])}

@app.put("/api/games/accounts")
def accounts(a:AccountSettings):
    s=get_settings().model_copy(update=a.model_dump()); put_settings(s)
    with connection() as db:
        for provider,user in (("lichess",a.lichess_username),("chess.com",a.chesscom_username)):
            if user: db.execute("INSERT INTO game_accounts(provider,username) VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET username=excluded.username",(provider,user))
            else: db.execute("DELETE FROM game_accounts WHERE provider=?",(provider,))
    return a

def store_games(provider,user,raw):
    stream,count=io.StringIO(raw),0
    with connection() as db:
        while game:=chess.pgn.read_game(stream):
            board=game.board(); start=board.fen(); moves=[m.uci() for m in game.mainline_moves()]; gid=game.headers.get("Site") or card_id(start,moves)
            color="white" if game.headers.get("White","").lower()==user.lower() else "black"; played=game.headers.get("UTCDate",game.headers.get("Date","1970.01.01")).replace('.','-')
            count+=db.execute("INSERT OR IGNORE INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,game_url,opening_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(gid,provider,user,played,game.headers.get("Event","unknown"),1,color,game.headers.get("Result","*"),start,json.dumps(moves),game.headers.get("Site"),game.headers.get("Opening"))).rowcount
    return count

def compare_games():
    """Classify the first game/repertoire divergence without changing card history."""
    with connection() as db:
        lines=[{**dict(r),"moves":json.loads(r["moves_json"])} for r in db.execute("SELECT * FROM repertoire_lines")]
        games=[{**dict(r),"moves":json.loads(r["moves_json"])} for r in db.execute("SELECT * FROM imported_games")]
        for game in games:
            candidates=[line for line in lines if line["trained_color"]==game["color"] and line["start_fen"].split(' ')[:4]==game["start_fen"].split(' ')[:4]]
            classification="no applicable repertoire"; divergence=None; expected=[]; board=chess.Board(game["start_fen"])
            if candidates:
                classification="covered"
                for ply,actual in enumerate(game["moves"]):
                    expected=sorted({line["moves"][ply] for line in candidates if len(line["moves"])>ply})
                    matching=[line for line in candidates if len(line["moves"])>ply and line["moves"][ply]==actual]
                    if not matching:
                        classification="player deviation" if (board.turn==chess.WHITE)==(game["color"]=="white") else "opponent repertoire gap"
                        divergence=(ply,board.fen(),actual); break
                    candidates=matching
                    try: board.push_uci(actual)
                    except ValueError: break
            db.execute("INSERT INTO repertoire_comparisons(game_id,repertoire_id,classification,divergence_ply,divergence_fen,expected_json,actual_uci,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET repertoire_id=excluded.repertoire_id,classification=excluded.classification,divergence_ply=excluded.divergence_ply,divergence_fen=excluded.divergence_fen,expected_json=excluded.expected_json,actual_uci=excluded.actual_uci,updated_at=excluded.updated_at",(game["id"],candidates[0]["repertoire_id"] if candidates else None,classification,divergence[0] if divergence else None,divergence[1] if divergence else None,json.dumps(expected),divergence[2] if divergence else None,datetime.now(timezone.utc).isoformat()))

@app.post("/api/games/sync")
async def sync(request:GameSyncRequest):
    imported=0; started=datetime.now(timezone.utc)
    with connection() as db:
        for provider,user in (("lichess",request.lichess_username),("chess.com",request.chesscom_username)):
            if user: db.execute("INSERT INTO game_sync_state(provider,status,last_started_at,last_error) VALUES(?,'syncing',?,NULL) ON CONFLICT(provider) DO UPDATE SET status='syncing',last_started_at=excluded.last_started_at,last_error=NULL",(provider,started.isoformat()))
    if request.lichess_username:
        with connection() as db: state=db.execute("SELECT cursor FROM game_sync_state WHERE provider='lichess'").fetchone()
        baseline=datetime.now(timezone.utc)-timedelta(days=request.days)
        if state and state[0]: baseline=max(baseline,datetime.fromisoformat(state[0])-timedelta(minutes=5))
        since=int(baseline.timestamp()*1000)
        params={"since":since,"moves":"true","opening":"true","perfType":','.join(request.speeds),"rated":str(request.rated_only).lower()}
        async with httpx.AsyncClient(timeout=45) as client: response=await client.get(f"https://lichess.org/api/games/user/{request.lichess_username}",params=params,headers={"Accept":"application/x-chess-pgn"})
        if response.status_code==404: raise HTTPException(404,"Lichess username not found")
        if response.status_code==429: raise HTTPException(429,"Lichess rate limit reached")
        if not response.is_success: raise HTTPException(response.status_code,"Lichess sync failed")
        imported+=store_games("lichess",request.lichess_username,response.text)
    if request.chesscom_username:
        cutoff=(datetime.now(timezone.utc)-timedelta(days=request.days)).date()
        async with httpx.AsyncClient(timeout=45,headers={"User-Agent":"Tempo local chess trainer"}) as client:
            archive_response=await client.get(f"https://api.chess.com/pub/player/{request.chesscom_username}/games/archives")
            if archive_response.status_code==404: raise HTTPException(404,"Chess.com username not found")
            if archive_response.status_code==429: raise HTTPException(429,"Chess.com rate limit reached")
            if not archive_response.is_success: raise HTTPException(archive_response.status_code,"Chess.com sync failed")
            for archive in archive_response.json().get("archives",[]):
                year,month=map(int,archive.rstrip('/').split('/')[-2:])
                if date(year,month,1)<date(cutoff.year,cutoff.month,1): continue
                response=await client.get(archive)
                if response.status_code==429: raise HTTPException(429,"Chess.com rate limit reached")
                response.raise_for_status()
                pgns='\n\n'.join(game.get("pgn","") for game in response.json().get("games",[]) if game.get("pgn"))
                imported+=store_games("chess.com",request.chesscom_username,pgns)
    compare_games()
    synced=datetime.now(timezone.utc)
    with connection() as db:
        for provider,user in (("lichess",request.lichess_username),("chess.com",request.chesscom_username)):
            if user: db.execute("UPDATE game_sync_state SET status='idle',cursor=?,last_success_at=?,last_error=NULL WHERE provider=?",((synced-timedelta(minutes=5)).isoformat(),synced.isoformat(),provider))
    return {"imported":imported,"cached":True,"incremental":True,"synced_at":synced}

@app.get("/api/games/sync/status")
def sync_status():
    with connection() as db: rows=db.execute("SELECT * FROM game_sync_state ORDER BY provider").fetchall()
    return {"providers":[dict(row) for row in rows]}

@app.post("/api/games/{game_id}/analysis")
def save_game_analysis(game_id:str,request:GameAnalysisRequest):
    with connection() as db:
        game=db.execute("SELECT color FROM imported_games WHERE id=?",(game_id,)).fetchone()
        if not game: raise HTTPException(404,"Game not found")
        threshold=db.execute("SELECT major_mistake_cp FROM settings WHERE id=1").fetchone()[0]
        result=classify_swings(request.evaluations,game[0],threshold)
        db.execute("DELETE FROM game_move_analysis WHERE game_id=?",(game_id,))
        for item in request.evaluations:
            loss=(int(item["before_cp"])-int(item["after_cp"]))*(1 if game[0]=="white" else -1)
            label="missed punishment" if int(item["ply"])==result["missed_punishment_ply"] else "major mistake" if int(item["ply"])==result["major_mistake_ply"] else None
            db.execute("INSERT INTO game_move_analysis VALUES(?,?,?,?,?,?,?)",(game_id,int(item["ply"]),int(item["before_cp"]),int(item["after_cp"]),loss,label,request.depth))
        db.execute("UPDATE imported_games SET analysis_state='ready',analysis_version=analysis_version+1,major_mistake_ply=?,missed_punishment_ply=? WHERE id=?",(result["major_mistake_ply"],result["missed_punishment_ply"],game_id))
    return result

@app.get("/api/games/summary")
def summary():
    with connection() as db: rows=db.execute("SELECT g.*,c.classification,c.divergence_ply,c.divergence_fen,c.expected_json,c.actual_uci FROM imported_games g LEFT JOIN repertoire_comparisons c ON c.game_id=g.id ORDER BY played_at DESC").fetchall()
    return {"total":len(rows),"games":[{**dict(row),"moves":json.loads(row["moves_json"])} for row in rows]}

@app.get("/api/games/{game_id}")
def game_detail(game_id:str):
    with connection() as db: row=db.execute("SELECT g.*,c.classification,c.divergence_ply,c.divergence_fen,c.expected_json,c.actual_uci FROM imported_games g LEFT JOIN repertoire_comparisons c ON c.game_id=g.id WHERE g.id=?",(game_id,)).fetchone()
    if not row: raise HTTPException(404,"Game not found")
    result=dict(row); result["moves"]=json.loads(result.pop("moves_json")); result["expected"]=json.loads(result.pop("expected_json") or "[]")
    return result
