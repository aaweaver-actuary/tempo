"""Games-owned read projection. No engine, provider, command or Study dependency."""
import base64
import json

from tempo_intelligence_database.adapters import games_read_projection as storage

from ..errors import DomainError
from ..identity import canonical_json


class GamesReader:
    def __init__(self, owner):
        self.owner = owner
        self.workspace = owner.workspace_id

    def _scope(self, database, run_id):
        row = storage.read_run_scope(database,run_id)
        if not row:
            raise DomainError("analysis_run_not_found",404)
        return row

    def scopes(self):
        with self.owner.transaction(read_only=True) as database:
            rows = storage.list_run_scopes(database)
        return {"schema_version":1,"workspace_id":self.workspace,"items":rows}

    def scope(self, run_id):
        with self.owner.transaction(read_only=True) as database:
            scope = self._scope(database,run_id)
        return {"schema_version":1,"workspace_id":self.workspace,**scope}

    def _input_scope(self, database, scope):
        return storage.read_input_scope(database,scope['input_id'],scope['input_revision'])

    def _game_page(self, database, scope, after, limit):
        return storage.read_game_page(database,scope['input_id'],scope['input_revision'],after,limit)

    def games(self, run_id, after=None, limit=10):
        if type(limit) is not int or not 1<=limit<=10:
            raise DomainError("invalid_analysis_game_limit",422)
        # Scope, accepted identity and game page share one owning read transaction.
        with self.owner.transaction(read_only=True) as database:
            run = self._scope(database,run_id)
            source = self._input_scope(database,run)
            if not source:
                raise DomainError("analysis_input_not_found",404)
            if source['state']!='accepted':
                raise DomainError("analysis_input_not_accepted")
            identity={'workspace':self.workspace,'run':run_id,'revision':run['revision'],
                'input_id':run['input_id'],'input_revision':run['input_revision'],'ordering':'game-id-v1'}
            last=''
            if after:
                try:
                    if len(after)>4096:raise ValueError()
                    cursor=json.loads(base64.urlsafe_b64decode(after.encode()))
                    last=cursor.pop('last')
                    if type(last) is not str or not last:raise ValueError()
                except Exception:raise DomainError('invalid_analysis_game_cursor',422) from None
                if cursor!=identity:raise DomainError('stale_analysis_game_cursor')
                if last not in source['game_ids']:raise DomainError('invalid_analysis_game_cursor',422)
            # PostgreSQL returns at most one lookahead beyond the requested page.
            # No complete input pages or engine progress enter the process.
            items=self._game_page(database,run,last,limit+1)
        next_after=base64.urlsafe_b64encode(canonical_json({**identity,'last':items[limit-1]['game_id']}).encode()).decode() if len(items)>limit else None
        return {'schema_version':1,'workspace_id':self.workspace,**run,
            'game_count':len(source['game_ids']),'selections':source['selections'],
            'items':items[:limit],'next_after':next_after}
