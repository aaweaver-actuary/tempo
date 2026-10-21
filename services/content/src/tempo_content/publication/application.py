"""Existing Content publication build/admission sequences over fixed operations."""
from collections.abc import Callable

from tempo_content_publication_contracts.v1 import Publication, serialize_contract

from ..domain import ContentError
from ..models import SourceSnapshot
from .preparation import prepare_publication, publication_authority

Hook = Callable[[str, dict], None]


def build_source_publication(source_key: str, hook: Hook | None, *, inventory_version: str,
                             read_source: Callable[[str], dict], read_task: Callable[[str, int], dict],
                             read_payload: Callable[[str, int], dict | None],
                             read_manifest: Callable[[str, int], dict],
                             reserve_build: Callable[[dict, dict], str],
                             read_snapshot: Callable[[dict], SourceSnapshot],
                             read_revisions: Callable[[str, int], dict[str, int]],
                             persist: Callable[[Publication, str, dict, Hook | None], None],
                             notify: Callable[..., None]) -> Publication:
    source = read_source(source_key)
    task = read_task(source['id'],source['requested_revision'])
    existing = read_payload(source['publication_id'],task['generation'])
    if existing:
        return Publication.model_validate_json(existing['payload'])
    if task['state']!='complete':
        raise ContentError('inventory_incomplete','Current inventory is not complete')
    manifest = read_manifest(source['id'],task['revision'])
    if manifest['counts']['exercises']==0:
        raise ContentError('retirement_required','Empty desired source requires Learning retirement through #193')
    built_at = reserve_build(source,task)
    snapshot = read_snapshot(manifest)
    revisions = read_revisions(source['id'],task['generation'])
    publication, provenance = prepare_publication(snapshot,task['revision'],task['generation'],built_at,revisions,
        source_id=source['id'],source_key=source_key,source_digest=manifest['digest'],inventory_version=inventory_version)
    notify(hook,'before_commit',publication=publication)
    persist(publication,source['id'],provenance,hook)
    return publication


def admit_publication(publication: Publication | dict, source_id: str, provenance: dict, hook: Hook | None, *,
                      inventory_version: str, digest_text: Callable[[str], str],
                      read_payload: Callable[[str, int], dict | None],
                      read_manifest: Callable[[str, int], dict],
                      read_revisions: Callable[[str, int], dict[str, int]],
                      read_snapshot: Callable[[dict], SourceSnapshot],
                      commit_publication: Callable[[Publication, str, dict, str, str, str | None, dict | None, Hook | None], None]) -> None:
    publication = Publication.model_validate(publication)
    payload = serialize_contract(publication)
    if isinstance(payload,bytes):
        payload = payload.decode()
    digest = digest_text(payload)
    # Reconstruct the exact authoritative definitions before the write
    # section; a contract-valid envelope alone is not build authority.
    replay = read_payload(publication.publication_id,publication.publication_generation)
    expected_payload = None
    expected_provenance = None
    if not replay:
        manifest = read_manifest(source_id,publication.source_revision)
        revisions = read_revisions(source_id,publication.publication_generation)
        expected_snapshot = read_snapshot(manifest)
        expected_payload, expected_provenance = publication_authority(expected_snapshot,publication,revisions,
            source_id=source_id,source_digest=manifest['digest'],inventory_version=inventory_version)
    commit_publication(publication,source_id,provenance,payload,digest,expected_payload,expected_provenance,hook)
