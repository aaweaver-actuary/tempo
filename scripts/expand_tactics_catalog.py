#!/usr/bin/env python3
"""Preserve the original catalog and reproducibly add Lichess CC0 packs."""
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from build_tactics_decks import valid, RANGES

GROUPS = [
    ('basic', 'Basic motifs'), ('advanced', 'Advanced motifs'),
    ('calculation', 'Calculation'), ('mating-depth', 'Mating depth'),
    ('endgames', 'Endgame tactics'), ('named-mates', 'Named mates'),
]
THEMES = {
    'basic': [('hangingPiece','Hanging pieces'),('fork','Forks'),('pin','Pins'),('skewer','Skewers'),('discoveredAttack','Discovered attack'),('trappedPiece','Trapped pieces'),('attackingF2F7','Attacking f2 or f7'),('capturingDefender','Capture the defender'),('doubleCheck','Double check'),('sacrifice','Sacrifice')],
    'advanced': [('attraction','Attraction'),('clearance','Clearance'),('deflection','Deflection'),('defensiveMove','Defensive move'),('interference','Interference'),('intermezzo','Intermezzo'),('quietMove','Quiet move'),('xRayAttack','X-Ray attack'),('zugzwang','Zugzwang')],
    'calculation': [(f'calculation{length}',f'{length}-move calculation') for length in (2,3,4)],
    'mating-depth': [('mateIn1','Mate in 1'),('mateIn2','Mate in 2'),('mateIn3','Mate in 3'),('mateIn4Plus','Mate in 4+')],
    'endgames': [('rookEndgame','Rook endgame'),('pawnEndgame','Pawn endgame')],
    'named-mates': [('anastasiaMate',"Anastasia’s mate"),('arabianMate','Arabian mate'),('backRankMate','Back rank mate'),('balestraMate','Balestra mate'),('blindSwineMate','Blind Swine mate'),('bodenMate',"Boden’s mate"),('cornerMate','Corner mate'),('doubleBishopMate','Double bishop mate'),('dovetailMate','Dovetail mate'),('epauletteMate','Epaulette mate'),('hookMate','Hook mate'),('killBoxMate','Kill box mate'),('pillsburysMate',"Pillsbury’s mate"),('morphysMate',"Morphy’s mate"),('operaMate','Opera mate'),('swallowstailMate',"Swallow’s tail mate"),('triangleMate','Triangle mate'),('vukovicMate','Vuković mate'),('smotheredMate','Smothered mate')],
}


def rows(source):
    process = subprocess.Popen(['zstd','-dc',str(source)], stdout=subprocess.PIPE)
    try:
        with io.TextIOWrapper(process.stdout, encoding='utf-8', newline='') as stream:
            yield from csv.DictReader(stream)
    finally:
        process.wait()
        if process.returncode:
            raise RuntimeError('Could not read pinned Lichess export')


def record_from_row(row):
    return {key: row[key] for key in ('PuzzleId','FEN','Moves','GameUrl')} | {
        key: int(row[key]) for key in ('Rating','RatingDeviation','Popularity','NbPlays')
    } | {'Themes': sorted(row['Themes'].split())}


def build(source, root):
    original = json.loads((root/'tactics-decks.json').read_text())
    original_ids = {record['PuzzleId'] for record in original}
    decks = {}
    for record in original:
        decks.setdefault(record['DeckId'], []).append(record)
    new_themes = [(group, theme, name) for group, entries in THEMES.items() for theme,name in entries if f'{theme}-easy' not in decks]
    pools = {theme: [] for _,theme,_ in new_themes}
    counts = {theme: 0 for theme in pools}
    rating_counts = {}
    # Bounded pools are selected in source order; named mates retain all candidates for relative rating selection.
    for row in rows(source):
        if row['PuzzleId'] in original_ids:
            continue
        matches = set(row['Themes'].split()) & pools.keys()
        rating = int(row['Rating'])
        for theme in matches:
            counts[theme] += 1
            named = theme in dict(THEMES['named-mates'])
            if named or 700 <= rating <= 2000:
                if named or rating_counts.get((theme,rating),0) < 30:
                    rating_counts[(theme,rating)] = rating_counts.get((theme,rating),0)+1
                    pools[theme].append(record_from_row(row))
    used = set(original_ids)
    for group,theme,_ in sorted(new_themes, key=lambda entry:(counts[entry[1]],entry[1])):
        available = sorted(pools[theme], key=lambda record:(record['Rating'],record['PuzzleId']))
        if group == 'named-mates':
            candidates = [record for record in available if record['PuzzleId'] not in used]
            easy = []
            for record in candidates:
                if valid(record):
                    easy.append(record)
                    if len(easy) == 50: break
            advanced = []
            for record in reversed(candidates):
                if record['PuzzleId'] not in {item['PuzzleId'] for item in easy} and valid(record):
                    advanced.append(record)
                    if len(advanced) == 50: break
            selected = {'easy':easy,'advanced':sorted(advanced,key=lambda record:(record['Rating'],record['PuzzleId']))}
        else:
            selected = {}
            for stage,(minimum,maximum) in dict(RANGES,focused=(1250,2000)).items():
                target = 250 if stage == 'focused' else 100
                selection = []
                for record in available:
                    if minimum <= record['Rating'] <= maximum and record['PuzzleId'] not in used and valid(record):
                        selection.append(record)
                        used.add(record['PuzzleId'])
                        if len(selection) == target: break
                selected[stage] = selection
        for stage,selection in selected.items():
            target = (50 if group == 'named-mates' else 250 if stage == 'focused' else 100)
            if len(selection) != target:
                raise RuntimeError(f'Incomplete {theme}-{stage}: {len(selection)}/{target}')
            used.update(record['PuzzleId'] for record in selection)
            decks[f'{theme}-{stage}'] = selection
        print(f'Allocated {theme}', flush=True)
    manifest = {'version':1,'groups':[{'id':key,'name':name} for key,name in GROUPS], 'themes':[], 'packs':[], 'source':{
        'url':'https://database.lichess.org/lichess_db_puzzle.csv.zst', 'retrievedAt':'2026-09-14',
        'sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(), 'license':'CC0-1.0',
        'selection':'Existing positions preserved. New themes allocated by ascending export frequency; general candidates in rating/ID order, capped at 30 candidates per rating. Named mates: 50 lowest and 50 highest rated eligible legal records. Global PuzzleId uniqueness.',
    }}
    (root/'tactics-packs').mkdir(exist_ok=True)
    for group,entries in THEMES.items():
        for theme,name in entries:
            manifest['themes'].append({'id':theme,'name':name,'group':group})
            stages = ('easy','advanced') if group == 'named-mates' else (*RANGES,'focused')
            for stage in stages:
                legacy = f'{theme}-{stage}'
                selection = sorted(decks[legacy],key=lambda record:record.get('DeckPosition',0))
                for offset in range(0,len(selection),25):
                    ordinal = offset//25+1
                    pack_id = f'{legacy}-{ordinal:02d}'
                    records = []
                    for position,record in enumerate(selection[offset:offset+25],1):
                        packaged = record | {'DeckId':pack_id,'DeckPosition':position,'Motif':theme,'Difficulty':stage}
                        if record['PuzzleId'] in original_ids:
                            packaged |= {'LegacyDeckId':legacy,'LegacyDeckPosition':record['DeckPosition']}
                        records.append(packaged)
                    asset = f'data/tactics-packs/{pack_id}.json'
                    (root.parent/asset).write_text(json.dumps(records,separators=(',',':')))
                    manifest['packs'].append({'id':pack_id,'theme':theme,'group':group,'difficulty':stage,'ordinal':ordinal,'count':25,'minRating':min(record['Rating'] for record in records),'maxRating':max(record['Rating'] for record in records),'asset':asset,'legacyDeckId':legacy})
    (root/'tactics-catalog.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(f"Wrote {len(manifest['packs'])} packs")

if __name__ == '__main__':
    build(Path(sys.argv[1]), Path(sys.argv[2]))
