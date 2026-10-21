def test_native_source_fingerprints_cover_relocated_database_inputs_and_hooks(tmp_path):
    import ast
    import hashlib
    from pathlib import Path

    script=Path(__file__).resolve().parents[1]/'tooling/run_postgres.py'
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
        ('source_inputs','database/tempo_game_evidence_database/tooling/run_postgres.py',
         tmp_path/'database/tempo_game_evidence_database/tooling/run_postgres.py'),
        ('source_inputs','database/tempo_game_evidence_database/tests/native/test_postgres.py',
         tmp_path/'database/tempo_game_evidence_database/tests/native/test_postgres.py'),
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
        namespace=dict(ROOT=tmp_path,SERVICE=service,HERE=tmp_path/'database/tempo_game_evidence_database/tooling',
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


def test_artifact_source_fingerprints_cover_relocated_database_owner(tmp_path):
    import ast
    import hashlib
    from pathlib import Path

    root=Path(__file__).resolve().parents[3]
    script=root/'services/game-evidence/integration/qualify_artifacts.py'
    main=next(node for node in ast.parse(script.read_text()).body
              if isinstance(node,ast.FunctionDef) and node.name=='main')
    capture=next(node for node in ast.walk(main) if isinstance(node,ast.For)
                 and isinstance(node.target,ast.Name) and node.target.id=='relative')
    relative_paths=[
        'services/game-evidence/hatch_build.py',
        'database/tempo_game_evidence_database/adapters/store.py',
        'database/tempo_game_evidence_database/ddl/migrations/001.sql',
        'database/tempo_game_evidence_database/tooling/run_postgres.py',
        'database/tempo_game_evidence_database/tests/native/test_postgres.py',
    ]
    for relative in relative_paths:
        path=tmp_path/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('synthetic source')

    def fingerprints():
        # Execute only the real hash loop; no build, native command or import runs.
        namespace=dict(ROOT=tmp_path,hashlib=hashlib,report={'source_inputs':{}})
        exec(compile(ast.Module(body=[capture],type_ignores=[]),str(script),'exec'),namespace)
        return namespace['report']['source_inputs']

    previous=fingerprints()
    for relative in relative_paths:
        assert relative in previous
        (tmp_path/relative).write_text('changed relocated input')
        current=fingerprints()
        assert current[relative]!=previous[relative]
        previous=current

