import io, json, uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import chess, chess.pgn, httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .database import connection, initialize
from .models import AccountSettings, BranchRequest, GameSyncRequest, ImportResult, ReviewRequest, Settings
from .services.analysis import AnalysisCapabilities
from .services.cards import card_id
from .services.pgn import parse_pgn, prefix_through_user_moves
from .services.scheduler import schedule_review, unlock_ready

@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize(); yield

app=FastAPI(title="Tempo local API",version="0.2.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:3000","http://127.0.0.1:3000"],allow_methods=["*"],allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status":"ok","storage":"local-sqlite","scheduler":"FSRS 6"}

@app.get("/api/analysis/capabilities")
def capabilities(): return AnalysisCapabilities()

@app.get("/api/settings",response_model=Settings)
def get_settings():
    with connection() as db: row=db.execute("SELECT initial_depth,timezone,new_cards_per_day,lichess_username,chesscom_username FROM settings WHERE id=1").fetchone()
    return Settings(**dict(row))

@app.put("/api/settings",response_model=Settings)
def put_settings(s: Settings):
    with connection() as db: db.execute("UPDATE settings SET initial_depth=?,timezone=?,new_cards_per_day=?,lichess_username=?,chesscom_username=? WHERE id=1",(s.initial_depth,s.timezone,s.new_cards_per_day,s.lichess_username,s.chesscom_username))
    return s

def seed_queue(db,day):
    maximum=db.execute("SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?",(day,)).fetchone()[0]
    rows=db.execute("SELECT id FROM cards WHERE due_date<=? AND state!='locked' AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id",(day,day)).fetchall()
    for offset,row in enumerate(rows,1): db.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",(day,row[0],maximum+offset))

@app.get("/api/queue/today")
def queue_today():
    day=date.today().isoformat()
    with connection() as db:
        seed_queue(db,day)
        rows=db.execute("SELECT q.id queue_entry_id,q.position,q.cycle,q.attempt_state,c.* FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=? AND q.status='queued' ORDER BY q.position,q.id",(day,)).fetchall()
    cards=[{**dict(r),"moves":json.loads(r["moves_json"])} for r in rows]
    for card in cards: card.pop("moves_json",None)
    return {"local_date":day,"cards":cards,"count":len(cards)}

@app.post("/api/imports/pgn",response_model=ImportResult)
async def import_pgn(file:UploadFile=File(...),trained_color:str=Form("white")):
    if not file.filename or not file.filename.lower().endswith('.pgn'): raise HTTPException(400,"Choose a .pgn file")
    if trained_color not in {"white","black"}: raise HTTPException(400,"trained_color must be white or black")
    games,lines=parse_pgn((await file.read()).decode("utf-8-sig"))
    if not lines: raise HTTPException(422,"No playable lines were found")
    rid,seen,created=str(uuid.uuid4()),set(),0; now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        depth=db.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]
        db.execute("INSERT INTO repertoires VALUES(?,?,?,?)",(rid,file.filename.rsplit('.',1)[0],file.filename,now))
        for line in lines:
            db.execute("INSERT OR IGNORE INTO repertoire_lines VALUES(?,?,?,?,?,?,?)",(card_id(line.starting_fen,line.moves),rid,file.filename,trained_color,line.starting_fen,json.dumps(line.moves),now))
            moves=prefix_through_user_moves(line.starting_fen,line.moves,trained_color,depth)
            if not moves: continue
            cid=card_id(line.starting_fen,moves)
            if cid in seen: continue
            seen.add(cid); created+=db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) VALUES(?,?,'prefix',?,?,?)",(cid,rid,line.starting_fen,json.dumps(moves),date.today().isoformat())).rowcount
    return ImportResult(repertoire_id=rid,source_name=file.filename,games_found=games,unique_lines=len(seen),cards_created=created,duplicates_merged=max(0,len(lines)-created))

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
        card=db.execute("SELECT interval_days,fsrs_card_json,first_correct_at,reinforcement_pending FROM cards WHERE id=?",(identifier,)).fetchone()
        if not card: raise HTTPException(404,"Card not found")
        s=schedule_review(request.outcome,interval_days=card[0],fsrs_card_json=card[1],first_correct_at=card[2],reinforcement_pending=bool(card[3]),reviewed_at=now)
        if request.queue_entry_id: db.execute("UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=? AND card_id=?",("guided" if request.guided else "clean",request.queue_entry_id,identifier))
        else: db.execute("UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position LIMIT 1)",(day,identifier))
        db.execute("INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval) VALUES(?,?,?,?,?,?,?)",(identifier,request.outcome,s.internal_rating,int(request.guided),now.isoformat(),card[0],s.interval_days))
        days=db.execute("SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct'",(identifier,)).fetchone()[0]
        recent=[r[0] for r in db.execute("SELECT rating FROM reviews WHERE card_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 2",(identifier,))]
        state="mature" if unlock_ready(s.stability,days,recent) else "learning"
        db.execute("UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,reinforcement_pending=?,stability=?,guided_review=?,state=? WHERE id=?",(s.due_date.isoformat(),s.interval_days,s.fsrs_card_json,s.first_correct_at,int(s.reinforcement_pending),s.stability,int(request.guided),state,identifier))
        if s.requeue_today: requeue(db,day,identifier,s.requeue_after_cards,"guided" if request.outcome=="again" else "reinforcement")
        if state=="mature": db.execute("UPDATE cards SET state='new',due_date=? WHERE unlock_after_card_id=? AND state='locked'",(day,identifier))
    return {"card_id":identifier,"next_due":s.due_date,"interval_days":s.interval_days,"state":state,"requeue_today":s.requeue_today,"requeue_after_cards":s.requeue_after_cards,"stability":s.stability}

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
    imported=0
    if request.lichess_username:
        since=int((datetime.now(timezone.utc)-timedelta(days=request.days)).timestamp()*1000)
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
    return {"imported":imported,"cached":True,"incremental":True,"synced_at":datetime.now(timezone.utc)}

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
