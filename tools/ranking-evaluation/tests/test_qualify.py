"""Qualifier-owned negative artifact proofs with tiny disposable wheels."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


def qualifier():
    specification=importlib.util.spec_from_file_location('ranking_qualify',Path(__file__).parents[1]/'qualify.py')
    module=importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def wheel(directory,name,requires=()):
    path=directory/(name+'-0.1.0-py3-none-any.whl')
    info=name+'-0.1.0.dist-info/'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr(info+'METADATA','Metadata-Version: 2.1\nName: '+name.replace('_','-')+'\nVersion: 0.1.0\n'+
            ''.join('Requires-Dist: '+requirement+'\n' for requirement in requires))
        archive.writestr(info+'WHEEL','Wheel-Version: 1.0\nGenerator: regression\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info+'RECORD','')
    return path


@pytest.mark.parametrize('changed',['tempo_evidence_math','locked_dependency'])
def test_qualifier_rejects_changed_locked_producer_or_dependency_before_candidate_install(tmp_path,changed):
    module=qualifier()
    wheelhouse=tmp_path/'wheels';wheelhouse.mkdir()
    producer=wheel(wheelhouse,'tempo_evidence_math',('locked-dependency==0.1.0',))
    dependency=wheel(wheelhouse,'locked_dependency')
    lock=tmp_path/'requirements.lock'
    lock.write_text(''.join(path.name.split('-0.1.0-')[0].replace('_','-')+'==0.1.0 --hash=sha256:'+
        hashlib.sha256(path.read_bytes()).hexdigest()+'\n' for path in (producer,dependency)))
    candidate=wheel(tmp_path,'tempo_ranking_evaluation',('tempo-evidence-math==0.1.0',))
    environment=tmp_path/'venv'
    subprocess.run([sys.executable,'-I','-B','-m','venv','--without-pip',str(environment)],check=True,capture_output=True,timeout=15)
    python=environment/'bin/python'
    # Positive control: exactly locked artifacts and the no-deps candidate work.
    module.install_candidate(python,wheelhouse,candidate,tmp_path,lock)
    assert '--require-hashes' in module.STAGES[-2]['command']
    assert '--no-deps' in module.STAGES[-1]['command']
    module.bounded(['uv','pip','uninstall','--python',str(python),'tempo-evidence-math','locked-dependency','tempo-ranking-evaluation'],tmp_path)
    changed_wheel=producer if changed=='tempo_evidence_math' else dependency
    with zipfile.ZipFile(changed_wheel,'a') as archive:archive.writestr('benign-extra-member.txt','different same-version bytes')
    before=len(module.STAGES)
    with pytest.raises(RuntimeError,match='Hash mismatch'):
        module.install_candidate(python,wheelhouse,candidate,tmp_path,lock)
    assert len(module.STAGES)==before+1  # No candidate installation or suite follows failure.
    error=module.STAGES[-1]['stderr']
    assert 'Expected' in error and 'Computed' in error
    assert changed.replace('_','-') in error
