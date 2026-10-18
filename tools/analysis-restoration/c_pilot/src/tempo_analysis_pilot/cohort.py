"""Deterministic bounded real-game selection from an authorized legacy snapshot.

This is an offline export adapter, not an importer or recurring synchronization.
Reconstructed PGNs retain original identities but are never called provider bytes.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import chess
import chess.pgn

SUPPORTED_PROVIDERS = {'chess.com', 'lichess'}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def select(games, *, accounts, since, until, primary=60, supplemental=20):
    if not accounts or not set(accounts) <= SUPPORTED_PROVIDERS or any(not isinstance(value,str) or not value.strip() for value in accounts.values()):
        raise ValueError("explicit_provider_accounts_required")
    if since.tzinfo is None or until.tzinfo is None or not timedelta(0) < until-since <= timedelta(days=366) or not 1 <= primary+supplemental <= 100:
        raise ValueError('bounded_aware_cohort_required')
    unique = {}
    for game in games:
        key = (game['provider'],game['provider_game_id'])
        if key in unique and unique[key] != game:
            raise ValueError('conflicting_provider_identity')
        unique[key] = game
    available = [game for game in unique.values() if game['provider'] in accounts and game['username'].casefold()==accounts[game['provider']].casefold()
        and since <= datetime.fromisoformat(game['played_at']) < until and game['rated']
        and game['result'] in ('1-0','0-1','1/2-1/2') and game['speed'] in ('rapid','classical','blitz')]
    available.sort(key=lambda game:(datetime.fromisoformat(game['played_at']),game['provider'],game['provider_game_id']),reverse=True)
    queues = defaultdict(deque)
    for game in available:
        if game['speed'] in ('rapid','classical'):
            queues[(game['provider'],game['color'])].append(game)
    chosen, used = [], set()
    while len(chosen) < primary and any(queues.values()):
        for key in sorted(queues):
            if queues[key] and len(chosen) < primary:
                game=queues[key].popleft();chosen.append((game,'representative','recent rated rapid/classical provider/color round robin'));used.add((game['provider'],game['provider_game_id']))
    strata = Counter((game['provider'],game['color'],game.get('opening_name')) for game,_,_ in chosen)
    for unused in range(primary+supplemental-len(chosen)):
        remaining=[game for game in available if (game['provider'],game['provider_game_id']) not in used]
        if not remaining:break
        # Stable ordering retains newest game for equal priority. Prefer rapid
        # and classical until supply is exhausted, then fill with blitz.
        game=min(remaining,key=lambda item:(item['speed']=='blitz',strata[(item['provider'],item['color'],item.get('opening_name'))]))
        chosen.append((game,'diagnostic','least represented provider/color/captured-opening stratum'));used.add((game['provider'],game['provider_game_id']));strata[(game['provider'],game['color'],game.get('opening_name'))]+=1
    chosen.sort(key=lambda item:(item[0]['played_at'],item[0]['provider'],item[0]['provider_game_id']))
    return chosen, {'captured_games':len(games),'unique_provider_games':len(unique),'eligible_available_games':len(available),
        'eligible_primary_games':sum(game['speed'] in ('rapid','classical') for game in available),
        'selected':len(chosen),'since':since.isoformat(),'until':until.isoformat(),'selection_before_findings':True}


def export_game(record):
    board=chess.Board(record['start_fen'])
    if not board.is_valid() or record['color'] not in ('white','black'):
        raise ValueError('invalid_start_or_player_color')
    game=chess.pgn.Game();game.setup(board)
    played=datetime.fromisoformat(record['played_at']).astimezone(timezone.utc)
    game.headers.update(Event='Authorized Tempo snapshot export',Site=record['game_url'],Result=record['result'],
        UTCDate=played.strftime('%Y.%m.%d'),UTCTime=played.strftime('%H:%M:%S'),TimeControl=str(record['time_control']),
        White=record['username'] if record['color']=='white' else 'Unknown opponent',
        Black=record['username'] if record['color']=='black' else 'Unknown opponent')
    node=game
    for value in json.loads(record['moves_json']):
        move=chess.Move.from_uci(value)
        if move not in board.legal_moves:raise ValueError('illegal_captured_move')
        node=node.add_variation(move);board.push(move)
    return (str(game)+'\n').encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts',type=Path,required=True);parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--since',required=True);parser.add_argument('--until',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();raw=args.capture.read_bytes();capture=json.loads(raw)
    games=capture['tables']['imported_games'];chosen,counts=select(games,accounts=json.loads(args.accounts.read_text()),since=datetime.fromisoformat(args.since),until=datetime.fromisoformat(args.until))
    args.output.mkdir(parents=True,exist_ok=True);(args.output/'pgn').mkdir(exist_ok=True)
    manifest=[]
    for record,stratum,reason in chosen:
        identity=hashlib.sha256(canonical([record['provider'],record['provider_game_id']]).encode()).hexdigest()
        item={'provider':record['provider'],'account':record['username'],'provider_game_id':record['provider_game_id'],'game_url':record['game_url'],
            'played_at_utc':record['played_at'],'time_control':record['time_control'],'rated':record['rated'],'player_color':record['color'],
            'opponent_identifier':None,'result':record['result'],'selection_stratum':stratum,'reason_selected':reason,
            'source_record_sha256':hashlib.sha256(canonical(record).encode()).hexdigest(),'capture_sha256':hashlib.sha256(raw).hexdigest(),
            'source_kind':'authorized_local_snapshot','original_provider_pgn_sha256':None,'legacy_game_id':record['id']}
        try:
            pgn=export_game(record);item.update(validation='valid',source_pgn_sha256=hashlib.sha256(pgn).hexdigest(),pgn_file='pgn/'+identity+'.pgn')
            destination=args.output/item['pgn_file']
            if destination.exists() and destination.read_bytes()!=pgn:raise ValueError('immutable_cohort_output_conflict')
            if not destination.exists():destination.write_bytes(pgn)
        except ValueError as error:
            item.update(validation='rejected',reason=str(error))
        manifest.append(item)
    for name,value in [('games-manifest.json',manifest),('selection-counts.json',counts)]:
        destination=args.output/name;encoded=(canonical(value)+'\n').encode()
        if destination.exists() and destination.read_bytes()!=encoded:raise ValueError('immutable_cohort_output_conflict')
        if not destination.exists():destination.write_bytes(encoded)
    print(json.dumps(counts))

if __name__=='__main__':main()
