"""Native accepted-scope exclusion owned by feedback persistence."""
import re

def owned_legacy_scope_sql(event_alias="event"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", event_alias):
        raise ValueError("event alias must be an SQL identifier")
    # Retained accepted contributions own the scope even after withdrawal or a
    # source correction. This is an ownership fence, never settlement policy.
    # A prefix equality survives both SQL translation and psycopg binding;
    # a raw LIKE wildcard is interpreted as a driver placeholder first.
    # Explicit JSONB extraction also keeps the compatibility compiler from
    # selecting PostgreSQL's JSON-only extraction overload for a JSONB value.
    return f"""EXISTS(SELECT 1 FROM remediation_contributions bridge_scope
        WHERE bridge_scope.workspace_id='legacy'
          AND bridge_scope.game_id={event_alias}.game_id
          AND LEFT(bridge_scope.publication_id,13)='legacy-graph:'
          AND jsonb_extract_path_text(bridge_scope.payload_json::jsonb,'authority','kind')='legacy_graph'
          AND jsonb_extract_path_text(bridge_scope.payload_json::jsonb,'authority','workspace_id')='legacy'
          AND jsonb_extract_path_text(bridge_scope.payload_json::jsonb,'authority','repertoire_id')={event_alias}.repertoire_id)"""
