"""One backend workspace; accepted bridge scopes never fall back to old misses."""
import re

LEGACY_WORKSPACE_ID = "legacy"
BRIDGE_MISS_REASON = "Priority review · accepted real-game obligation"


def owned_legacy_scope_sql(event_alias="event"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", event_alias):
        raise ValueError("event alias must be an SQL identifier")
    # Retained accepted contributions own the scope even after withdrawal or a
    # source correction. This is an ownership fence, never settlement policy.
    # A prefix equality survives both SQL translation and psycopg binding;
    # a raw LIKE wildcard is interpreted as a driver placeholder first.
    return f"""EXISTS(SELECT 1 FROM remediation_contributions bridge_scope
        WHERE bridge_scope.workspace_id='legacy'
          AND bridge_scope.game_id={event_alias}.game_id
          AND LEFT(bridge_scope.publication_id,13)='legacy-graph:'
          AND bridge_scope.payload_json::jsonb->'authority'->>'kind'='legacy_graph'
          AND bridge_scope.payload_json::jsonb->'authority'->>'workspace_id'='legacy'
          AND bridge_scope.payload_json::jsonb->'authority'->>'repertoire_id'={event_alias}.repertoire_id)"""
