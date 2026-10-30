from dataclasses import dataclass, field
from pathlib import Path
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Configuration:
    workspace_id: str
    database_url: str = field(repr=False)
    reader_url: str = field(repr=False)
    token: str = field(repr=False)
    origins: tuple[str, ...] = ("http://localhost:5173",)
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    timezone: str = "America/New_York"
    pool_size: int = 4
    maintenance: bool = True

    operator_token: str | None = field(default=None, repr=False)
    evidence_token: str | None = field(default=None, repr=False)
    host_activity_directory: Path | None = None

    def __post_init__(self):
        if not self.workspace_id or len(self.workspace_id) > 200:
            raise ValueError("Configure one workspace identity")
        if len(self.token) < 32:
            raise ValueError(
                "Learning bearer credential must have at least 32 characters"
            )
        if not self.database_url or not self.reader_url:
            raise ValueError("Configure Learning writer and reader URLs")
        if not 1 <= self.pool_size <= 16:
            raise ValueError("Learning pool size must be 1..16")
        if not self.origins or any(
            origin == "*" or not origin.startswith(("http://", "https://"))
            for origin in self.origins
        ):
            raise ValueError("Configure exact allowed browser origins")
        for origin in self.origins:
            parsed = urlsplit(origin)
            if (
                not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise ValueError("Origins must be exact scheme/host/port values")
        for credential in (self.operator_token, self.evidence_token):
            if credential is not None and (
                len(credential) < 32 or credential == self.token
            ):
                raise ValueError("Operator/evidence credentials need distinct scopes")
        if self.operator_token and self.operator_token == self.evidence_token:
            raise ValueError("Operator/evidence credentials need distinct scopes")
        if not self.hosts or any(
            not host or "*" in host or "/" in host for host in self.hosts
        ):
            raise ValueError("Configure exact allowed hosts")
        ZoneInfo(self.timezone)

    @classmethod
    def environment(cls):
        from .host_activity import default_directory
        token_file = Path(os.environ["TEMPO_LEARNING_TOKEN_FILE"])
        return cls(
            workspace_id=os.environ["TEMPO_LEARNING_WORKSPACE"],
            database_url=os.environ["TEMPO_LEARNING_DATABASE_URL"],
            reader_url=os.environ["TEMPO_LEARNING_READER_URL"],
            token=token_file.read_text().strip(),
            host_activity_directory=default_directory(),
            origins=tuple(os.environ["TEMPO_LEARNING_ORIGINS"].split(",")),
            hosts=tuple(
                os.environ.get("TEMPO_LEARNING_HOSTS", "localhost,127.0.0.1").split(",")
            ),
            timezone=os.environ.get("TEMPO_LEARNING_TIMEZONE", "America/New_York"),
            pool_size=int(os.environ.get("TEMPO_LEARNING_POOL_SIZE", "4")),
            maintenance=os.environ.get("TEMPO_LEARNING_MAINTENANCE", "1") == "1",
            operator_token=Path(os.environ["TEMPO_LEARNING_OPERATOR_TOKEN_FILE"])
            .read_text()
            .strip()
            if os.environ.get("TEMPO_LEARNING_OPERATOR_TOKEN_FILE")
            else None,
            evidence_token=Path(os.environ["TEMPO_LEARNING_EVIDENCE_TOKEN_FILE"])
            .read_text()
            .strip()
            if os.environ.get("TEMPO_LEARNING_EVIDENCE_TOKEN_FILE")
            else None,
        )
