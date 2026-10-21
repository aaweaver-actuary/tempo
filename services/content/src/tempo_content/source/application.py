"""Existing source preflight/staging/manifest/admission over fixed operations."""
from collections.abc import Callable

from ..models import SourceSnapshot
from ..policy import prepare_source

Hook = Callable[[str, dict], None]


def load_source_snapshot(snapshot: SourceSnapshot | dict, expected_revision: int, hook: Hook | None,
                         operation_id: str | None, *, workspace_id: str, kinds: tuple[str, ...],
                         digest_text: Callable[[str], str],
                         preflight: Callable[[str, str, int], dict | None],
                         stage_page: Callable[[str, str, dict, int], None],
                         seal_manifest: Callable[[str, str, str, dict, dict], None],
                         admit_source: Callable[[SourceSnapshot, str, str, str, str, int, Hook | None, str | None], tuple[int, int, str, bool]],
                         notify: Callable[..., None]) -> dict:
    snapshot, payload, digest, source_id, publication_id, stage_id = prepare_source(
        snapshot, expected_revision, workspace_id, digest_text=digest_text)
    current = preflight(source_id,digest,expected_revision)
    if current:
        return {'source_id':source_id,'source_revision':current['requested_revision'],'generation':current['generation'],'status':current['state'],'replayed':True}
    counts = {kind:len(payload[kind]) for kind in kinds}
    # Each staged section is bounded. A crash before manifest/admission leaves
    # only immutable resumable input, never a requested source revision.
    for kind in kinds:
        for offset in range(0,counts[kind],64):
            stage_page(stage_id,kind,payload,offset)
    header = {key:value for key,value in payload.items() if key not in kinds}
    seal_manifest(stage_id,source_id,digest,header,counts)
    notify(hook,'after_staging',source_id=source_id,stage_id=stage_id)
    revision, generation, state, replayed = admit_source(
        snapshot,source_id,publication_id,digest,stage_id,expected_revision,hook,operation_id)
    return {'source_id':source_id,'source_revision':revision,'generation':generation,'status':state,'replayed':replayed}
