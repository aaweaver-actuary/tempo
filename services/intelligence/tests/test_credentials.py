"""Private-file credentials never escape through representations or failures."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tempo_intelligence.credentials import CredentialError, PrivateCredentials


SECRET = "synthetic_private_credential"


def private_file(tmp_path, **changes):
    directory = tmp_path.resolve() / "private"
    directory.mkdir(mode=0o700)
    path = directory / "credential.json"
    payload = {"schema_version": 1, "registration_id": str(uuid4()), "token": SECRET}
    payload.update(changes)
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    return path, payload


def test_private_credential_registration_and_aware_expiry_are_loaded_without_secret_repr(tmp_path):
    path, payload = private_file(tmp_path, expires_at="2030-01-01T03:00:00+03:00")
    material = PrivateCredentials(path).load()
    assert material.registration_id == payload["registration_id"]
    assert material.token == SECRET
    assert material.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert SECRET not in repr(material)
    path.chmod(0o400)
    assert PrivateCredentials(path).load() == material


def test_private_credential_restart_load_preserves_registration_and_expired_material(tmp_path):
    path, _ = private_file(tmp_path, expires_at="2000-01-01T00:00:00Z")
    first = PrivateCredentials(path).load()
    assert PrivateCredentials(path).load() == first
    assert first.expires_at < datetime.now(timezone.utc)


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"registration_id": "not-a-uuid"},
    {"registration_id": None}, {"token": ""}, {"token": "secret\nheader"},
    {"token": "has space"}, {"token": 9}, {"token": "x" * 4097},
    {"expires_at": "2030-01-01T00:00:00"}, {"expires_at": "secret invalid"},
    {"expires_at": 17}, {"unexpected": SECRET},
])
def test_private_credential_invalid_schema_errors_are_fixed_and_secret_free(tmp_path, changes):
    path, _ = private_file(tmp_path, **changes)
    with pytest.raises(CredentialError) as caught:
        PrivateCredentials(path).load()
    assert caught.value.code == "credential_file_invalid"
    assert str(caught.value) == "credential_file_invalid"
    assert SECRET not in repr(caught.value)
    assert str(path) not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o777])
def test_private_credential_rejects_nonprivate_file_permissions(tmp_path, mode):
    path, _ = private_file(tmp_path)
    path.chmod(mode)
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()


def test_private_credential_requires_private_parent_and_path_outside_repository(tmp_path):
    path, _ = private_file(tmp_path)
    path.parent.chmod(0o755)
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()
    path.parent.chmod(0o700)
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path, forbidden_root=tmp_path.resolve()).load()
    assert PrivateCredentials(path, forbidden_root=tmp_path.resolve() / "repo").load().token == SECRET


def test_private_credential_rejects_relative_paths_and_missing_configuration(tmp_path):
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(Path("credential.json")).load()
    with pytest.raises(CredentialError, match="credential_missing"):
        PrivateCredentials(None).load()
    with pytest.raises(CredentialError, match="credential_storage_unavailable"):
        PrivateCredentials(tmp_path.resolve() / "missing.json").load()


def test_private_credential_rejects_symlink_file_and_symlink_parent(tmp_path):
    path, _ = private_file(tmp_path)
    link = path.parent / "link.json"
    link.symlink_to(path)
    with pytest.raises(CredentialError):
        PrivateCredentials(link).load()
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(CredentialError):
        PrivateCredentials(alias / path.name).load()


def test_private_credential_rejects_nonregular_and_oversize_or_duplicate_json(tmp_path):
    path, _ = private_file(tmp_path)
    path.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()
    path.write_text('{"schema_version":1,"token":"first","token":"second"}')
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()
    path.unlink()
    os.mkfifo(path, mode=0o600)
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()


def test_private_credential_atomic_replacement_requires_new_explicit_identity(tmp_path):
    path, payload = private_file(tmp_path)
    before = PrivateCredentials(path).load()
    replacement = path.with_name("replacement.json")
    payload.update(registration_id=str(uuid4()), token="new_synthetic_credential")
    replacement.write_text(json.dumps(payload))
    replacement.chmod(0o600)
    replacement.replace(path)
    after = PrivateCredentials(path).load()
    assert before.registration_id != after.registration_id
    assert before.token == SECRET and after.token == "new_synthetic_credential"


def test_private_credential_rejects_repository_without_explicit_forbidden_root(tmp_path):
    path, _ = private_file(tmp_path)
    (tmp_path / ".git").write_text("gitdir: synthetic-private-worktree")
    with pytest.raises(CredentialError, match="credential_file_invalid"):
        PrivateCredentials(path).load()
