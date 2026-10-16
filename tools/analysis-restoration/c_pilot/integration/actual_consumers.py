"""Explicit actual producer acceptance: no fixture or live-service fallback."""
import argparse
import json
import os
import chess
from pathlib import Path
from tempo_analysis_pilot import normalize
from tempo_analysis_pilot.stockfish_client import connect
from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import stockfish_input_digest


def main(arguments=None):
    parser=argparse.ArgumentParser()

    for flag,variable in (('a-export','TEMPO_C_A_EXPORT'),('game-ids','TEMPO_C_GAME_IDS_FILE'),('b-exchange','TEMPO_C_B_EXCHANGE'),('output','TEMPO_C_ACTUAL_EVIDENCE')):
        parser.add_argument('--'+flag,type=Path,default=os.environ.get(variable),required=variable not in os.environ)
    args=parser.parse_args(arguments);raw=json.loads((args.a_export/'manifest.json').read_text());ids=json.loads(args.game_ids.read_text())
    manifest,pages=normalize(args.a_export,workspace=raw['workspace_id'],selected_game_ids=ids)
    exchange=json.loads(args.b_exchange.read_text());assert exchange['scope']=='actual installed Stockfish output'
    jobs={};accepted=0
    for record in exchange['records']:
        if record['model']=='StockfishJob':
            job=v1.StockfishJob.model_validate(record['payload']);assert job.input_digest==stockfish_input_digest(job);jobs[(job.job_id,job.execution_generation)]=job
        elif record['model']=='StockfishResult':
            result=v1.StockfishResult.model_validate(record['payload']);job=jobs[(result.job_id,result.execution_generation)]
            assert result.workspace_id==job.workspace_id and result.request_revision==job.request_revision and result.provenance.input_digest==job.input_digest and result.engine_version==job.engine_version
            board=chess.Board(job.fen)
            for move in job.moves:board.push_uci(move)
            for variation in result.variations:
                assert variation.score_perspective=='side_to_move'
                if job.search_moves:assert variation.moves[0] in job.search_moves
                replay=board.copy()
                for move in variation.moves:replay.push_uci(move)
            accepted+=1
    assert accepted>0 and pages
    args.output.write_text(json.dumps({'case':'C_actual_AB_consumers','passed':True,'A_manifest_digest':raw['manifest_digest'],'A_provenance':raw['provenance'],'A_games':len(ids),'B_result_records':accepted,'note':'Serialized actual artifact acceptance; durable fresh execution is separately reported'},indent=2)+'\n')

if __name__=='__main__':main()
