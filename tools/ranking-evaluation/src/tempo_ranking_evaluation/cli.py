"""Installed, bounded offline evaluation. Capture remains external tooling."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from tempo_evidence_math import (OpeningRankingError, canonical_json, load_snapshot, PrecomputedValuationScorer)
from .ranking import ProductionOrderScorer, ShorterCardTestScorer, rank, compare
from .harness import run_corpus, MAX_FILE_BYTES, MAX_REPORT_BYTES, MAX_TOP_K_VALUES
from .resources import installed_corpus_directory
from .replay import score_snapshot

SCORERS={'production-order':ProductionOrderScorer,'shorter-card-test':ShorterCardTestScorer,'valuation':PrecomputedValuationScorer}


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise OpeningRankingError('invalid_arguments',message)


def check_top_k(values):
    if len(values)>MAX_TOP_K_VALUES:
        raise OpeningRankingError('limit_exceeded','At most 16 K values are permitted')
    if not values or any(type(value) is not int or value<1 or value>2**53-1 for value in values) or len(set(values))!=len(values):
        raise OpeningRankingError('invalid_top_k','K values must be distinct positive exact integers')


def check_expansion(size,bundles,top_k,*,multiplier=1):
    if size*(bundles+1)*(len(top_k)+4)*multiplier>MAX_REPORT_BYTES:
        raise OpeningRankingError('limit_exceeded','Projected report expansion exceeds 64 MiB before ranking')


def read_snapshot(path):
    with path.open('rb') as source:raw=source.read(MAX_FILE_BYTES+1)
    if len(raw)>MAX_FILE_BYTES:raise OpeningRankingError('limit_exceeded','Snapshot exceeds 4 MiB')
    document=json.loads(raw)
    if not isinstance(document,dict):raise OpeningRankingError('invalid_input','Snapshot must be an object')
    return document,len(raw)


def emit_report(result,output):
    # Complete bounded serialization precedes any final output creation/stdout.
    chunks=[];size=0
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    for chunk in encoder.iterencode(result):
        encoded=chunk.encode('utf-8');size+=len(encoded)
        if size+1>MAX_REPORT_BYTES:raise OpeningRankingError('limit_exceeded','Serialized report exceeds 64 MiB')
        chunks.append(encoded)
    rendered=b''.join(chunks)+b'\n'
    if output is None:
        sys.stdout.buffer.write(rendered)
        sys.stdout.buffer.flush()
        return
    created=False
    try:
        with output.open('xb') as destination:
            created=True
            destination.write(rendered)
    except BaseException:
        if created:output.unlink(missing_ok=True)
        raise


def main(arguments=None):
    try:
        parser=Parser(description=__doc__,allow_abbrev=False)
        commands=parser.add_subparsers(dest='command',required=True)
        corpus=commands.add_parser('corpus',allow_abbrev=False)
        corpus.add_argument('--corpus',type=Path)
        corpus.add_argument('--scenario',action='append')
        comparison=commands.add_parser('compare',allow_abbrev=False)
        comparison.add_argument('--snapshot',type=Path,required=True)
        comparison.add_argument('--scorer',choices=SCORERS,action='append')
        score=commands.add_parser('score',allow_abbrev=False)
        score.add_argument('--snapshot',type=Path,required=True)
        for command in (corpus,comparison,score):
            command.add_argument('--top-k',type=int,action='append')
            command.add_argument('--output',type=Path)
        options=parser.parse_args(arguments)
        top_k=tuple(options.top_k or (1,5,10));check_top_k(top_k)
        if options.output is not None and (options.output.exists() or options.output.is_symlink()):
            raise OpeningRankingError('invalid_output','Output must not exist; inputs and prior reports are protected')
        if options.command=='corpus':
            manifest=options.corpus if options.corpus is not None else installed_corpus_directory()/'manifest.json'
            result=run_corpus(manifest,scenario_ids=options.scenario,top_k=top_k)
        else:
            document,size=read_snapshot(options.snapshot)
            if options.command=='score':
                # Bound repeated per-candidate/per-decision provenance before calculation.
                candidates=document.get('candidates',());routes=document.get('routes',());readiness=document.get('readiness',())
                multiplier=(len(candidates)+1)*(len(routes)+len(readiness)+1)
                check_expansion(size,2,top_k,multiplier=multiplier)
                result=score_snapshot(document,top_k)
            else:
                scorers=options.scorer or ['production-order','shorter-card-test']
                if len(set(scorers))!=len(scorers):raise OpeningRankingError('duplicate_scorer','Scorers must be unique')
                check_expansion(size,len(scorers),top_k)
                candidates,context=load_snapshot(document)
                result=compare(candidates,[rank(candidates,context,SCORERS[name]()) for name in scorers],top_k)
                result.update(snapshot_id=context.snapshot_id,as_of=context.as_of,study_day=context.study_day,
                    production_order_basis=document['production_order_basis'],
                    actual_admission_plan=document['actual_admission_plan'],actual_admission_plan_basis=document['actual_admission_plan_basis'],
                    source_versions=document['source_versions'],historical_counterfactual_supported=False,limitations=document['limitations'])
        emit_report(result,options.output)
        return 0
    except OpeningRankingError as error:
        code,message=error.code,str(error)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError) as error:
        code,message='invalid_input_or_output','Unable to validate local inputs or create bounded output ('+type(error).__name__+')'
    sys.stderr.write(canonical_json({'status':'error','code':code,'message':message[:2048]})+'\n')
    return 2


if __name__=='__main__':raise SystemExit(main())
