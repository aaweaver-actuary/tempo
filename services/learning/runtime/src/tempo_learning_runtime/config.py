from dataclasses import dataclass
from pathlib import Path
import os
from zoneinfo import ZoneInfo

@dataclass(frozen=True)
class Configuration:
    workspace_id: str
    database_url: str
    reader_url: str
    token: str
    origins: tuple[str,...] = ("http://localhost:5173",)
    hosts: tuple[str,...] = ("localhost","127.0.0.1","testserver")
    timezone: str = "America/New_York"
    pool_size: int = 4
    maintenance: bool = True
    tablebase_url: str | None = "https://tablebase.lichess.ovh/standard"

    def __post_init__(self):
        if not self.workspace_id or len(self.workspace_id)>200: raise ValueError("Configure one workspace identity")
        if len(self.token)<32: raise ValueError("Learning bearer credential must have at least 32 characters")
        if not self.database_url or not self.reader_url: raise ValueError("Configure Learning writer and reader URLs")
        if not 1<=self.pool_size<=16: raise ValueError("Learning pool size must be 1..16")
        if not self.origins or any(origin=='*' or not origin.startswith(('http://','https://')) for origin in self.origins): raise ValueError("Configure exact allowed browser origins")
        ZoneInfo(self.timezone)

    @classmethod
    def environment(cls):
        token_file=Path(os.environ['TEMPO_LEARNING_TOKEN_FILE'])
        return cls(workspace_id=os.environ['TEMPO_LEARNING_WORKSPACE'],
            database_url=os.environ['TEMPO_LEARNING_DATABASE_URL'],reader_url=os.environ['TEMPO_LEARNING_READER_URL'],
            token=token_file.read_text().strip(),
            origins=tuple(os.environ['TEMPO_LEARNING_ORIGINS'].split(',')),
            hosts=tuple(os.environ.get('TEMPO_LEARNING_HOSTS','localhost,127.0.0.1').split(',')),
            timezone=os.environ.get('TEMPO_LEARNING_TIMEZONE','America/New_York'),
            pool_size=int(os.environ.get('TEMPO_LEARNING_POOL_SIZE','4')),
            maintenance=os.environ.get('TEMPO_LEARNING_MAINTENANCE','1')=='1',
            tablebase_url=os.environ.get('TEMPO_LEARNING_TABLEBASE_URL','https://tablebase.lichess.ovh/standard') or None)
