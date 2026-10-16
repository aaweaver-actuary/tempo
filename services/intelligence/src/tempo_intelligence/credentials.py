"""Load private provider material without putting secrets in durable state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from uuid import UUID


MAX_CREDENTIAL_BYTES = 16 * 1024


class CredentialError(ValueError):
    """A fixed public error code; filenames and file contents are never included."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CredentialMaterial:
    registration_id: str
    token: str = field(repr=False)
    expires_at: datetime | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class PrivateCredentials:
    """Read a configured immutable registration from an owner-only private file.

    Operators replace the file atomically, choose a fresh registration UUID for
    every material/expiry change, and explicitly activate it through the service.
    Reading the file does not activate registrations or contact the provider.
    """

    def __init__(self, path: Path | None, forbidden_root: Path | None = None):
        self.path = Path(path) if path is not None else None
        self.forbidden_root = Path(forbidden_root) if forbidden_root is not None else None

    def _read(self) -> bytes:
        if self.path is None:
            raise CredentialError("credential_missing")
        if not self.path.is_absolute():
            raise CredentialError("credential_file_invalid")
        normalized = Path(os.path.abspath(self.path))
        # Installed runtimes do not necessarily know a source checkout path.
        # Reject files inside any Git worktree even without forbidden_root.
        if any((parent / ".git").exists() for parent in normalized.parents):
            raise CredentialError("credential_file_invalid")
        if self.forbidden_root is not None:
            root = self.forbidden_root.resolve()
            if normalized.is_relative_to(root):
                raise CredentialError("credential_file_invalid")
        directory_fd = None
        file_fd = None
        try:
            # Open every directory without following symlinks. A later rename
            # cannot switch the directory/file underneath this descriptor.
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open("/", directory_flags)
            for component in normalized.parts[1:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            parent = os.fstat(directory_fd)
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise CredentialError("credential_file_invalid")
            file_fd = os.open(
                normalized.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            metadata = os.fstat(file_fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
                    or metadata.st_nlink != 1 or metadata.st_size > MAX_CREDENTIAL_BYTES):
                raise CredentialError("credential_file_invalid")
            contents = bytearray()
            while len(contents) <= MAX_CREDENTIAL_BYTES:
                part = os.read(file_fd, MAX_CREDENTIAL_BYTES + 1 - len(contents))
                if not part:
                    break
                contents.extend(part)
            if len(contents) > MAX_CREDENTIAL_BYTES:
                raise CredentialError("credential_file_invalid")
            after = os.fstat(file_fd)
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode) != (
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode
            ):
                raise CredentialError("credential_file_invalid")
            return bytes(contents)
        except CredentialError:
            raise
        except OSError:
            raise CredentialError("credential_storage_unavailable") from None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def load(self) -> CredentialMaterial:
        contents = self._read()
        try:
            payload = json.loads(contents, object_pairs_hook=_unique_object)
            if not isinstance(payload, dict) or set(payload) - {
                "schema_version", "registration_id", "token", "expires_at"
            }:
                raise ValueError("invalid fields")
            if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
                raise ValueError("invalid version")
            registration_id = payload["registration_id"]
            if not isinstance(registration_id, str):
                raise ValueError("invalid registration")
            registration_uuid = UUID(registration_id)
            if registration_uuid.version != 4 or str(registration_uuid) != registration_id:
                raise ValueError("invalid registration")
            token = payload["token"]
            if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                    or any(character.isspace() or not 33 <= ord(character) <= 126 for character in token)):
                raise ValueError("invalid token")
            expires_at = payload.get("expires_at")
            if expires_at is not None:
                if not isinstance(expires_at, str):
                    raise ValueError("invalid expiry")
                expires_at = datetime.fromisoformat(expires_at)
                if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                    raise ValueError("invalid expiry")
                expires_at = expires_at.astimezone(timezone.utc)
            return CredentialMaterial(registration_id, token, expires_at)
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
            raise CredentialError("credential_file_invalid") from None
