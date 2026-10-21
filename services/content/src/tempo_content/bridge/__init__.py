"""Public bridge composition; application policy and database operations have separate homes."""
from .application import (
    BridgeApplication,
    Provenance,
    RouteAssignment,
    Reentry,
    ImportManifest,
    ResponseProposal,
    canonical,
    digest,
    map_import,
    validate_bridge_execution_scope,
    append_export_scope,
    pack_bridge_document,
    unpack_bridge_document,
    projected_definition,
    MAX_BRIDGE_DOCUMENT_BYTES,
)
from tempo_content_database.adapters.bridge import BridgeStore, lock_scope


class Bridge(BridgeApplication):
    def __init__(self, service):
        super().__init__(service, BridgeStore(service))
