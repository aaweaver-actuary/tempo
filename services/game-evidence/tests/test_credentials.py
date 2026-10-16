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
