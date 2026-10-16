import pytest
from tempo_game_evidence.delivery import learning_token


def test_learning_credential_is_loaded_from_private_file_without_changing_its_identity(
    tmp_path,
):
    path = tmp_path / "evidence-token"
    path.write_text("credential-" + "x" * 40 + "\n")
    path.chmod(0o600)
    assert learning_token(path) == "credential-" + "x" * 40


@pytest.mark.parametrize(
    "contents,mode",
    [("x" * 40, 0o644), ("short", 0o600), ("x" * 40 + "\nembedded", 0o600)],
)
def test_invalid_or_shared_learning_credential_file_fails_before_delivery(
    tmp_path, contents, mode
):
    path = tmp_path / "evidence-token"
    path.write_text(contents)
    path.chmod(mode)
    with pytest.raises(ValueError):
        learning_token(path)


@pytest.mark.parametrize('failure', ['unconfigured', 'missing-file', 'invalid', 'shared'])
def test_work_with_learning_endpoint_requires_private_credential_before_mutation(tmp_path, monkeypatch, capsys, failure):
    from tempo_game_evidence import cli
    calls = []
    class Store:
        def __init__(self, *_):
            pass
        def process_one(self):
            calls.append('process')
            return False
    monkeypatch.setattr(cli, 'EvidenceStore', Store)
    monkeypatch.setattr(cli, 'import_one', lambda *a, **kw: calls.append('import'))
    monkeypatch.setattr(cli, 'deliver_one', lambda *a, **kw: calls.append('delivery'))
    monkeypatch.delenv('TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE', raising=False)
    arguments = ['work', '--once', '--learning-endpoint', 'http://127.0.0.1:1/internal/evidence']
    secret = 'fixture-secret-' + 'q' * 40
    if failure != 'unconfigured':
        path = tmp_path / 'private-token'
        if failure != 'missing-file':
            path.write_text('short' if failure == 'invalid' else secret)
            path.chmod(0o644 if failure == 'shared' else 0o600)
        arguments += ['--learning-token-file', str(path)]
    assert cli.main(arguments) != 0
    assert calls == []
    assert secret not in capsys.readouterr().out


def test_work_without_learning_endpoint_remains_producer_only(monkeypatch):
    from tempo_game_evidence import cli
    calls = []
    class Store:
        def __init__(self, *_):
            pass
        def process_one(self):
            calls.append('comparison')
            return False
    monkeypatch.setattr(cli, 'EvidenceStore', Store)
    monkeypatch.setattr(cli, 'import_one', lambda *a, **kw: calls.append('import'))
    monkeypatch.delenv('TEMPO_GAME_EVIDENCE_LEARNING_ENDPOINT', raising=False)
    monkeypatch.delenv('TEMPO_GAME_EVIDENCE_LEARNING_TOKEN_FILE', raising=False)
    assert cli.main(['work', '--once']) == 0
    assert calls == ['import', 'comparison']


@pytest.mark.parametrize('token', [None, '', 'short', 'x' * 40 + '\nembedded'])
def test_direct_delivery_missing_credential_never_claims(token):
    from tempo_game_evidence.delivery import deliver_one
    class Store:
        def connect(self):
            pytest.fail('credential failure claimed database work')
    with pytest.raises(ValueError):
        deliver_one(Store(), 'http://127.0.0.1:1/internal/evidence', token=token)
    assert not deliver_one(Store(), None, token=token)
