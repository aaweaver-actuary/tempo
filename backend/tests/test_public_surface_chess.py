"""Neutral public identities and cold import; no policy or runtime activation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
for relative in ('backend', 'packages/chess-core/src'):
    sys.path.insert(0, str(ROOT / relative))


def test_python_chess_surface_identity_and_neutral_semantics():
    from app.chess import interfaces as public
    from app.chess import pgn, position_identity
    from tempo_chess_core import interfaces as identity
    from tempo_chess_core import card_id, canonical_fen_key
    assert public.parse_pgn is pgn.parse_pgn
    assert public.position_key is position_identity.position_key
    assert public.card_id is identity.card_id is card_id
    assert identity.canonical_fen_key is canonical_fen_key
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    assert identity.card_id(fen, ['e2e4', 'e7e5']) == '4f1415440718daa4b13b5cf818644caccae3f5968d6756159ff366a2a10bda09'
    assert public.parse_pgn('1. e4 e5 *') == pgn.parse_pgn('1. e4 e5 *')
    assert not hasattr(public, 'find_defensive_knight_forks')
    assert not hasattr(public, 'classify_swings')


def test_neutral_cold_import_starts_no_provider_process_or_connection():
    code = '''
import sys,socket,subprocess
sys.path[:0]=PATHS
from unittest.mock import patch
def deny(*args,**kwargs):raise AssertionError('startup forbidden')
with patch.object(socket.socket,'connect',deny),patch.object(subprocess,'Popen',deny):
 import app.chess.interfaces,tempo_chess_core.interfaces
assert 'app.main' not in sys.modules
assert 'app.contracts.defense' not in sys.modules
'''.replace('PATHS', repr([str(ROOT/'backend'), str(ROOT/'packages/chess-core/src')]))
    subprocess.run([sys.executable, '-c', code], check=True, timeout=5)


def test_chess_line_preserves_runtime_exports_and_replay():
    script = r'''
const ts=require(process.argv[1]+'/node_modules/typescript');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=process.argv[1],out=process.argv[2];
for(const name of ['index.ts','interfaces/index.ts']){
 const text=fs.readFileSync(root+'/packages/chess-line/src/'+name,'utf8');
 const target=out+'/'+name.replace('.ts','.js');fs.mkdirSync(path.dirname(target),{recursive:true});
 fs.writeFileSync(target,ts.transpileModule(text,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText);
}
fs.symlinkSync(root+'/node_modules',out+'/node_modules','dir');
const old=require(out+'/index.js'),pub=require(out+'/interfaces/index.js');
assert.deepEqual(Object.keys(pub).sort(),['canonicalFenKey','canonicalizeMoves','sanForUci']);
for(const key of Object.keys(pub))assert.equal(pub[key],old[key]);
const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
assert.deepEqual(pub.canonicalizeMoves(fen,['e4','e5']).moves,['e2e4','e7e5']);
'''
    with tempfile.TemporaryDirectory(prefix='tempo-chess-surface-') as directory:
        subprocess.run(['node', '-e', script, str(ROOT), directory], check=True, timeout=5)


def test_rust_surface_is_explicit_neutral_and_registration_remains_pending():
    source = (ROOT/'tempo-core/src/interfaces/mod.rs').read_text()
    assert 'pub use crate::structs::{LineDiagnostic, ValidatedLine};' in source
    for name in ('card_id_native','position_distance_native','prefix_native','validate_uci_line_native','canonical_fen_key_native'):
        assert name in source
    assert 'pub use crate::*' not in source
    assert 'fsrs_core_available' not in source
