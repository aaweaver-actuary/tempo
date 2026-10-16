"""A serialized analysis boundary: source-free integrity and mixed-page rejection."""
from copy import deepcopy
import json

import pytest
from tempo_game_evidence.analysis_schema import AnalysisManifest,AnalysisPage,verify_directory
from tempo_game_evidence.analysis_export import digest,canonical,KINDS,write_exact


def export_fixture(directory):
    pages=[]
    counts={kind:0 for kind in KINDS}
    for kind in ('definitions','games','positions','comparisons'):
        page={'schema_version':1,'input_id':'a'*64,'workspace_id':'fixture','revision':1,'kind':kind,'page_index':0,'items':[{'source':'synthetic'}]}
        filename=f'{kind}-000000.json'
        write_exact(directory/filename,page)
        pages.append({'kind':kind,'page_index':0,'digest':digest(page),'filename':filename,'count':1})
        counts[kind]=1
    manifest={'schema_version':1,'kind':'tempo-analysis-inputs','input_id':'a'*64,'revision':1,'workspace_id':'fixture',
        'source':{'source_id':'source','source_revision':1},'publication':{'publication_id':'pub','publication_generation':1},'acceptance':{},
        'publication_digest':'b'*64,'content_export_digest':'c'*64,'snapshot':{'revision':1},'activation':{},'activation_origin':{},
        'current_serving':{'status':'unavailable'},'complete':False,'readiness':'unknown','provenance':{'kind':'synthetic'},'counts':counts,'pages':pages}
    manifest['manifest_digest']=digest(manifest)
    write_exact(directory/'manifest.json',manifest)
    return manifest


def test_A_analysis_export_replay_is_offline_and_inspectable(tmp_path):
    manifest=export_fixture(tmp_path)
    assert verify_directory(tmp_path)['manifest_digest']==manifest['manifest_digest']
    assert AnalysisManifest.model_validate(manifest).readiness=='unknown'
    assert verify_directory(tmp_path)['counts']['games']==1
    write_exact(tmp_path/'manifest.json',manifest)
    with pytest.raises(ValueError,match='export_output_conflict'):
        write_exact(tmp_path/'manifest.json',{**manifest,'revision':2})


@pytest.mark.parametrize('fault',['mixed-input','wrong-workspace','unsupported-version','digest','missing-page','missing-count'])
def test_A_analysis_export_rejects_mixed_or_incomplete_generation(tmp_path,fault):
    manifest=export_fixture(tmp_path)
    page_path=tmp_path/'positions-000000.json'
    if fault=='missing-page':
        page_path.unlink()
    elif fault=='missing-count':
        manifest['counts']['positions']=2
        manifest['manifest_digest']=digest({key:value for key,value in manifest.items() if key!='manifest_digest'})
        (tmp_path/'manifest.json').write_text(canonical(manifest))
    else:
        page=json.loads(page_path.read_text())
        if fault=='mixed-input': page['input_id']='d'*64
        if fault=='wrong-workspace': page['workspace_id']='foreign'
        if fault=='unsupported-version': page['schema_version']=True
        if fault=='digest': page['items'][0]['source']='changed'
        page_path.write_text(canonical(page))
    with pytest.raises((ValueError,OSError)):
        verify_directory(tmp_path)
