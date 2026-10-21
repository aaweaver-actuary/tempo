import subprocess
import sys


def test_installed_import_never_initializes_optional_domains_or_network():
    guard="""
import sys
def forbid(event,args):
    if event in {'socket.connect','socket.bind','subprocess.Popen','sqlite3.connect'}:
        raise AssertionError(event)
sys.addaudithook(forbid)
import tempo_game_evidence
assert not any(name.split('.')[0] in {'app','tempo_learning_review','tempo_scheduling','celery','redis'} for name in sys.modules)
"""
    result=subprocess.run([sys.executable,"-I","-c",guard],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_native_source_fingerprints_cover_relocated_database_inputs_and_hooks(tmp_path):
    import ast
    import hashlib
    from pathlib import Path

    script=Path(__file__).resolve().parents[1]/'integration/run_postgres.py'
    main=next(node for node in ast.parse(script.read_text()).body
              if isinstance(node,ast.FunctionDef) and node.name=='main')
    assignments=[node for node in main.body if isinstance(node,ast.Assign)
        and isinstance(node.targets[0],ast.Subscript)
        and isinstance(node.targets[0].value,ast.Name) and node.targets[0].value.id=='report'
        and isinstance(node.targets[0].slice,ast.Constant)
        and node.targets[0].slice.value in {'source_inputs','receiver_inputs'}]
    assert len(assignments)==2
    service=tmp_path/'services/game-evidence'
    learning=tmp_path/'services/learning/remediation'
    cases=[
        ('source_inputs','database/tempo_game_evidence_database/adapters/store.py',
         tmp_path/'database/tempo_game_evidence_database/adapters/store.py'),
        ('source_inputs','database/tempo_game_evidence_database/ddl/migrations/001.sql',
         tmp_path/'database/tempo_game_evidence_database/ddl/migrations/001.sql'),
        ('source_inputs','hatch_build.py',service/'hatch_build.py'),
        ('receiver_inputs','database/tempo_remediation_database/adapters/repository.py',
         tmp_path/'database/tempo_remediation_database/adapters/repository.py'),
        ('receiver_inputs','database/tempo_remediation_database/ddl/migrations/001_remediation.sql',
         tmp_path/'database/tempo_remediation_database/ddl/migrations/001_remediation.sql'),
        ('receiver_inputs','hatch_build.py',learning/'hatch_build.py'),
    ]
    for path in [*(path for _field,_key,path in cases),
                 service/'src/tempo_game_evidence/__init__.py',learning/'src/tempo_learning_remediation/__init__.py']:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('synthetic source')

    def capture():
        # Compile only the real input-map assignments; native commands never run.
        namespace=dict(ROOT=tmp_path,SERVICE=service,HERE=service/'integration',
                       learning=learning,hashlib=hashlib,report={})
        exec(compile(ast.Module(body=assignments,type_ignores=[]),str(script),'exec'),namespace)
        return namespace['report']

    previous=capture()
    assert 'src/tempo_game_evidence/__init__.py' in previous['source_inputs']
    assert 'src/tempo_learning_remediation/__init__.py' in previous['receiver_inputs']
    for field,key,path in cases:
        assert key in previous[field]
        path.write_text('changed relocated input')
        current=capture()
        assert current[field][key]!=previous[field][key]
        previous=current
