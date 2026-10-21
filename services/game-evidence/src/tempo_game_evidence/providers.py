"""Explicit account windows and durable source-first provider checkpoints."""
from tempo_game_evidence_database.adapters import providers as storage
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.parse import quote
import uuid

import httpx
from pydantic import Field, model_validator
from typing import Literal

from .snapshot import StrictModel
from .source import MAX_SOURCE_BYTES, identity, normalize_pgn, utc_now
from .transport import RemoteFailure, request_bytes


class AccountImport(StrictModel):
    workspace_id: str = Field(min_length=1)
    provider: Literal["lichess", "chess.com"]
    account: str = Field(min_length=1, max_length=100)
    since: datetime
    until: datetime
    page_size: int = Field(default=100, ge=1, le=1000)
    rated_only: bool = False

    @model_validator(mode="after")
    def bounded_window(self):
        if self.since.utcoffset() is None or self.until.utcoffset() is None:
            raise ValueError("explicit_ordered_timezone_aware_window_required")
        self.since = self.since.astimezone(timezone.utc)
        self.until = self.until.astimezone(timezone.utc)
        if self.until <= self.since:
            raise ValueError("explicit_ordered_timezone_aware_window_required")
        if self.until-self.since > timedelta(days=366):
            raise ValueError("split_imports_into_windows_at_most_366_days")
        return self


def enqueue_import(store, config):
    document = config.model_dump(mode="json")
    import_id = identity(document)
    since_ms = int(config.since.timestamp()*1000)
    cursor = {"since_ms": since_ms, "seen": []} if config.provider == "lichess" else {
        "year": config.since.year, "month": config.since.month, "offset": 0}
    with store.connect() as database:
        storage.enqueue_import(database, import_id, config.workspace_id, document, cursor)
    return import_id


def cancel_import(store, import_id):
    with store.connect() as database:
        storage.cancel_import(database, import_id)


def import_one(store, *, client=None, user_agent="tempo-game-evidence/0.1.0 (+https://github.com/aaweaver-actuary/tempo)", cancelled=lambda: False):
    # A provider lease serializes all requests from this service, across accounts.
    token = uuid.uuid4().hex
    with store.connect() as database:
        row = storage.claimable_import(database)
        if not row:
            return False
        config = AccountImport.model_validate_json(json.dumps(row["configuration"]))
        locked = storage.acquire_provider_lease(database, config.provider, token)
        if not locked:
            return False
        storage.begin_import_attempt(database, token, row['import_id'])
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15,read=10),follow_redirects=False,
                                   headers={"User-Agent":user_agent})
    try:
        if cancelled():
            raise RemoteFailure("import_cancelled", terminal=True)
        cursor = row["cursor"]
        page_id = identity({key:value for key,value in cursor.items() if key != "offset"})
        with store.connect() as database:
            saved = storage.saved_import_page(database, row['import_id'], page_id)
        if saved:
            raw = bytes(saved["raw_response"])
        else:
            username = quote(config.account.casefold(), safe="")
            if config.provider == "lichess":
                params = {"since":cursor["since_ms"],"until":int(config.until.timestamp()*1000)-1,
                          "max":config.page_size,"sort":"dateAsc","pgnInJson":"true","moves":"true",
                          "ongoing":"false","finished":"true","evals":"false"}
                if config.rated_only:
                    params["rated"] = "true"
                raw = request_bytes(client,"GET",f"https://lichess.org/api/games/user/{username}",
                                    max_bytes=MAX_SOURCE_BYTES,params=params,headers={"Accept":"application/x-ndjson"})
            else:
                raw = request_bytes(client,"GET",f"https://api.chess.com/pub/player/{username}/games/{cursor['year']}/{cursor['month']:02d}",
                                    max_bytes=MAX_SOURCE_BYTES)
        # Validate the whole bounded page, including unconsumed month entries.
        try:
            _validate_page(config,raw)
            records, next_cursor, complete = _page(config,cursor,raw)
        except (ValueError, KeyError, TypeError, RemoteFailure) as error:
            _save_page(store,row,config,token,page_id,raw,'invalid',
                       error.reason if isinstance(error,RemoteFailure) else 'invalid_provider_page')
            raise
        _save_page(store,row,config,token,page_id,raw,'valid')
        prepared = []
        for source_id,pgn,played_at in records:
            if cancelled():
                raise RemoteFailure("import_cancelled",terminal=True)
            source_bytes = pgn.encode()
            try:
                document = normalize_pgn(source_bytes,config.account,played_at=played_at)
            except (ValueError, UnicodeError) as error:
                document = {"source_error":str(error),"player":config.account.casefold(),"played_at":None}
            game_id = identity(config.workspace_id,config.provider,source_id,config.account.casefold())
            source_digest = hashlib.sha256(source_bytes).hexdigest()
            document.update(workspace_id=config.workspace_id,game_id=game_id,provider=config.provider,
                            provider_game_id=source_id,source_digest=source_digest)
            prepared.append((source_id,source_bytes,played_at,document,game_id,source_digest))
        with store.connect() as database:
            current = storage.locked_import(database, row['import_id'])
            if current["status"] != "cancelled" and current["lease_token"] == token:
                provider = storage.locked_provider_lease(database, config.provider)
                if not provider or provider["token"]!=token or not provider["valid"]:
                    raise RemoteFailure("provider_lease_expired")
                for source_id,source_bytes,played_at,document,game_id,source_digest in prepared:
                    store._import(database,config.workspace_id,config.provider,source_id,config.account,source_bytes,
                                  source_digest,identity(source_digest,str(played_at)),game_id,document)
                storage.complete_import_page(database, next_cursor, 'completed' if complete else 'pending', row['import_id'])
    except RemoteFailure as error:
        with store.connect() as database:
            if error.reason == "remote_rate_limited":
                storage.extend_provider_cooldown(database, error.retry_after, config.provider)
            storage.record_import_failure(database, 'pending' if error.reason=='import_cancelled' else 'blocked' if error.terminal else 'retrying', error.reason, utc_now()+timedelta(seconds=max(error.retry_after,min(900,2**min(row['attempts'],10)))), row['import_id'], token)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        with store.connect() as database:
            storage.record_unavailable_source(database, row['import_id'], token)
    finally:
        if owned:
            client.close()
        with store.connect() as database:
            storage.release_import_lease(database, row['import_id'], token)
            storage.release_provider_lease(database, config.provider, token)
    return True


def _page(config, cursor, raw):
    if config.provider == "lichess":
        games = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if len(games)>config.page_size:
            raise RemoteFailure("provider_page_bound_exceeded",terminal=True)
        times = [int(game["createdAt"]) for game in games]
        if times != sorted(times) or any(timestamp<cursor["since_ms"] or timestamp>=int(config.until.timestamp()*1000) for timestamp in times):
            raise RemoteFailure("unverified_provider_order_or_window",terminal=True)
        records = [(game["id"],game["pgn"],datetime.fromtimestamp(game["createdAt"]/1000,timezone.utc).isoformat())
                   for game in games if not (game["createdAt"] == cursor["since_ms"] and game["id"] in cursor["seen"])]
        if len(games)==config.page_size and not records:
            raise RemoteFailure("saturated_timestamp_boundary_requires_larger_page",terminal=True)
        next_cursor = {"since_ms": times[-1] if times else cursor["since_ms"],
                       "seen": sorted(set((cursor["seen"] if times and times[-1] == cursor["since_ms"] else [])
                                          + [game["id"] for game in games if times and game["createdAt"] == times[-1]]))}
        return records,next_cursor,len(games)<config.page_size
    games = json.loads(raw)["games"]
    if not isinstance(games,list) or len(games)>10000:
        raise RemoteFailure("provider_month_bound_exceeded",terminal=True)
    records = []
    for game in games[cursor["offset"]:cursor["offset"]+64]:
        timestamp = datetime.fromtimestamp(game["end_time"],timezone.utc)
        if config.since<=timestamp<config.until and game.get("rules","chess")=="chess" and (not config.rated_only or game.get("rated")):
            source_id = game.get("uuid") or game.get("url","").rstrip("/").split("/")[-1]
            if not source_id:
                raise RemoteFailure("provider_game_identity_missing",terminal=True)
            records.append((source_id,game["pgn"],timestamp.isoformat()))
    next_cursor = {**cursor,"offset":cursor["offset"]+64}
    if next_cursor["offset"]>=len(games):
        month = cursor["month"]%12+1
        year = cursor["year"]+(cursor["month"]==12)
        next_cursor = {"year":year,"month":month,"offset":0}
    complete = datetime(next_cursor["year"],next_cursor["month"],1,tzinfo=timezone.utc)>=config.until
    return records,next_cursor,complete


def _validate_page(config, raw):
    """A checkpoint proves structural validity, not PGN legality."""
    if config.provider == "lichess":
        games = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if len(games)>config.page_size:
            raise RemoteFailure("provider_page_bound_exceeded",terminal=True)
        timestamp_field, identity_field = "createdAt", "id"
    else:
        document = json.loads(raw)
        games = document["games"]
        if not isinstance(games,list):
            raise ValueError("provider_games_list_required")
        if len(games)>10000:
            raise RemoteFailure("provider_month_bound_exceeded",terminal=True)
        timestamp_field, identity_field = "end_time", "uuid"
    for game in games:
        if not isinstance(game,dict) or type(game.get(timestamp_field)) is not int:
            raise ValueError("provider_timestamp_required")
        source_id = game.get(identity_field) or (game.get("url") if config.provider=="chess.com" else None)
        if not isinstance(source_id,str) or not source_id or not isinstance(game.get("pgn"),str):
            raise ValueError("provider_identity_and_pgn_required")
        try:
            datetime.fromtimestamp(game[timestamp_field]/(1000 if config.provider=="lichess" else 1),timezone.utc)
        except (OverflowError,OSError):
            raise ValueError("provider_timestamp_invalid") from None


def _save_page(store,row,config,token,page_id,raw,status,reason=None):
    # An expired worker may retain its bytes but cannot replace a newer checkpoint.
    with store.connect() as database:
        current=storage.import_lease_state(database, row['import_id'])
        provider=storage.provider_lease_state(database, config.provider)
        if current['status']=='cancelled':
            raise RemoteFailure('import_cancelled',terminal=True)
        if current['lease_token']!=token or not current['valid'] or not provider or provider['token']!=token or not provider['valid']:
            raise RemoteFailure('provider_lease_expired')
        storage.save_import_page(database, row['import_id'], page_id, raw, hashlib.sha256(raw).hexdigest(), status, reason)
