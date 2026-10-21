"""Existing publication construction and provenance without storage effects."""
from tempo_content_publication_contracts.v1 import Publication, serialize_contract

from ..domain import ContentError, build_publication
from ..models import SourceSnapshot


def prepare_publication(snapshot: SourceSnapshot, source_revision: int, generation: int,
                        built_at: str, revisions: dict[str, int], *, source_id: str,
                        source_key: str, source_digest: str, inventory_version: str) -> tuple[Publication, dict]:
    if len(revisions)!=len(snapshot.exercises):
        raise ContentError('incomplete_exercises','Missing validated exercise revisions')
    publication = build_publication(snapshot,source_revision,generation,built_at,revisions)
    payload = serialize_contract(publication)
    if not isinstance(payload,str):
        payload = payload.decode()
    provenance = {'source_id':source_id,'source_key':source_key,'source_digest':source_digest,'source_revision':source_revision,'inventory_version':inventory_version,'snapshot':snapshot.model_dump(mode='json')}
    return publication, provenance


def publication_authority(expected_snapshot: SourceSnapshot, publication: Publication,
                          revisions: dict[str, int], *, source_id: str,
                          source_digest: str, inventory_version: str) -> tuple[str, dict]:
    expected_payload = serialize_contract(build_publication(expected_snapshot,publication.source_revision,publication.publication_generation,publication.built_at,revisions))
    expected_provenance = {'source_id':source_id,'source_key':expected_snapshot.source_key,'source_digest':source_digest,'source_revision':publication.source_revision,'inventory_version':inventory_version,'snapshot':expected_snapshot.model_dump(mode='json')}
    return expected_payload, expected_provenance
