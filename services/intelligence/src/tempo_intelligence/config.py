"""Explicit workspace configuration; private material never appears in reprs."""

from dataclasses import dataclass, field
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


def private_text(path: Path, *, maximum_bytes: int = 65536) -> str:
    """Read an owner-only regular file without following a final symlink."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("private_file_requires_absolute_path")
    descriptor = None
    directory_descriptor = None
    try:
        if any((parent / ".git").exists() for parent in path.parents):
            raise ValueError("private_file_invalid")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_descriptor = os.open("/", directory_flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        parent = os.fstat(directory_descriptor)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError("private_file_invalid")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_descriptor)
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or stat.S_IMODE(information.st_mode) not in {0o400, 0o600}
            or information.st_uid != os.geteuid()
            or information.st_nlink != 1
            or information.st_size > maximum_bytes
        ):
            raise ValueError("private_file_invalid")
        with os.fdopen(descriptor, "rb") as private_file:
            descriptor = None
            content = private_file.read(maximum_bytes + 1)
        if len(content) > maximum_bytes:
            raise ValueError("private_file_invalid")
        value = content.decode("utf-8").strip()
        if not value:
            raise ValueError("private_file_empty")
        return value
    except (OSError, UnicodeError):
        raise ValueError("private_file_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


@dataclass(frozen=True)
class Configuration:
    workspace_id: str
    database_url: str = field(repr=False)
    caller_token: str = field(repr=False)
    operator_token: str = field(repr=False)
    worker_token: str = field(repr=False)
    credential_file: Path | None = field(default=None, repr=False)
    provider_base_url: str = "https://explorer.lichess.org"
    model_profiles: tuple[dict, ...] = ()
    pool_size: int = 4
    background_pool_size: int = 1
    poll_interval: float = 1.0
    worker_enabled: bool = True
    origins: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]", "testserver")

    def __post_init__(self):
        if not isinstance(self.workspace_id, str) or not self.workspace_id.strip() or len(self.workspace_id) > 200:
            raise ValueError("explicit_workspace_required")
        if not self.database_url:
            raise ValueError("explicit_intelligence_database_required")
        tokens = (self.caller_token, self.operator_token, self.worker_token)
        if any(not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(not 33 <= ord(character) <= 126 for character in token) for token in tokens) or len(set(tokens)) != 3:
            raise ValueError("distinct_scoped_credentials_required")
        if self.credential_file is not None:
            credential_file = Path(self.credential_file)
            if not credential_file.is_absolute():
                raise ValueError("credential_file_requires_absolute_path")
            object.__setattr__(self, "credential_file", credential_file)
        if type(self.pool_size) is not int or not 1 <= self.pool_size <= 16:
            raise ValueError("invalid_pool_size")
        if type(self.background_pool_size) is not int or not 1 <= self.background_pool_size <= 2:
            raise ValueError("invalid_background_pool_size")
        if type(self.poll_interval) not in (float, int) or not math.isfinite(self.poll_interval) or not 0.05 <= self.poll_interval <= 60:
            raise ValueError("invalid_poll_interval")
        if type(self.worker_enabled) is not bool:
            raise ValueError("invalid_worker_enabled")
        if not self.hosts or any(not isinstance(host, str) or not host or "*" in host or "/" in host for host in self.hosts):
            raise ValueError("exact_allowed_hosts_required")
        for origin in self.origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or "*" in origin:
                raise ValueError("exact_browser_origins_required")
        try:
            endpoint = urlsplit(self.provider_base_url)
            port = endpoint.port
            loopback = endpoint.hostname == "localhost" or ipaddress.ip_address(endpoint.hostname or "").is_loopback
        except ValueError:
            loopback = False
            try:
                endpoint = urlsplit(self.provider_base_url)
                port = endpoint.port
            except ValueError:
                raise ValueError("invalid_provider_endpoint") from None
        if (
            not endpoint.hostname or any(character.isspace() for character in self.provider_base_url) or endpoint.username or endpoint.password
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment
            or endpoint.scheme not in {"https", "http"}
            or endpoint.scheme == "http" and not loopback
            or port is not None and not 1 <= port <= 65535
        ):
            raise ValueError("invalid_provider_endpoint")
        host=endpoint.hostname.lower()
        authority="["+host+"]" if ":" in host else host
        if port is not None and port != (443 if endpoint.scheme=="https" else 80):
            authority += ":"+str(port)
        object.__setattr__(self, "provider_base_url", endpoint.scheme+"://"+authority)
        profiles = tuple(self.model_profiles)
        identities = set()
        normalized_profiles = []
        for profile in profiles:
            if not isinstance(profile, dict) or set(profile) != {"model_id", "model_version", "artifact_sha256", "ratings"}:
                raise ValueError("invalid_model_profile")
            identity = (profile["model_id"], profile["model_version"], profile["artifact_sha256"])
            if any(not isinstance(value, str) or not value for value in identity) or not re.fullmatch("[0-9a-f]{64}", identity[2]):
                raise ValueError("invalid_model_profile")
            ratings = profile["ratings"]
            if not isinstance(ratings, (list, tuple)) or not ratings or any(type(rating) is not int or not 0 <= rating <= 4000 for rating in ratings) or len(set(ratings)) != len(ratings):
                raise ValueError("invalid_model_profile")
            if identity[:2] in identities:
                raise ValueError("duplicate_model_profile")
            identities.add(identity[:2])
            normalized_profiles.append({**profile, "ratings": list(ratings)})
        object.__setattr__(self, "model_profiles", tuple(normalized_profiles))

    @classmethod
    def environment(cls):
        try:
            configured = lambda name: os.environ["TEMPO_INTELLIGENCE_" + name]
            profiles_file = os.environ.get("TEMPO_INTELLIGENCE_MODEL_PROFILES_FILE")
            profiles = json.loads(private_text(Path(profiles_file))) if profiles_file else []
            if not isinstance(profiles, list):
                raise ValueError("invalid_model_profiles")
            enabled = os.environ.get("TEMPO_INTELLIGENCE_WORKER_ENABLED", "1")
            if enabled not in {"0", "1"}:
                raise ValueError("invalid_worker_enabled")
            return cls(
                workspace_id=configured("WORKSPACE"),
                database_url=private_text(Path(configured("DATABASE_URL_FILE"))),
                caller_token=private_text(Path(configured("CALLER_TOKEN_FILE"))),
                operator_token=private_text(Path(configured("OPERATOR_TOKEN_FILE"))),
                worker_token=private_text(Path(configured("WORKER_TOKEN_FILE"))),
                credential_file=Path(configured("CREDENTIAL_FILE")) if os.environ.get("TEMPO_INTELLIGENCE_CREDENTIAL_FILE") else None,
                provider_base_url=os.environ.get("TEMPO_INTELLIGENCE_PROVIDER_BASE_URL", "https://explorer.lichess.org"),
                model_profiles=tuple(profiles),
                pool_size=int(os.environ.get("TEMPO_INTELLIGENCE_POOL_SIZE", "4")),
                background_pool_size=int(os.environ.get("TEMPO_INTELLIGENCE_BACKGROUND_POOL_SIZE", "1")),
                poll_interval=float(os.environ.get("TEMPO_INTELLIGENCE_POLL_INTERVAL", "1")),
                worker_enabled=enabled == "1",
                origins=tuple(filter(None, os.environ.get("TEMPO_INTELLIGENCE_ORIGINS", "").split(","))),
                hosts=tuple(os.environ.get("TEMPO_INTELLIGENCE_HOSTS", "localhost,127.0.0.1,[::1]").split(",")),
            )
        except KeyError:
            raise ValueError("required_intelligence_configuration_missing") from None
