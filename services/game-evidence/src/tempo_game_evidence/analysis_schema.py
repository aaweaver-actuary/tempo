"""Service-local analysis-input v1 schemas and offline integrity verification."""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .analysis_export import digest, KINDS, PAGE_SIZE

PageKind = Literal['definitions','routes','legal_replies','games','positions','comparisons','deviations']


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def integer_versions(cls,value):
        if isinstance(value,dict) and any(key in value and type(value[key]) is not int for key in ('schema_version','revision')):
            raise ValueError('version_and_revision_require_integers')
        return value


class PageReference(Model):
    kind: PageKind
    page_index: int = Field(ge=0)
    digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    filename: str = Field(pattern=r'^(definitions|routes|legal_replies|games|positions|comparisons|deviations)-[0-9]{6}\.json$')
    count: int = Field(ge=1,le=PAGE_SIZE)


class AnalysisPage(Model):
    schema_version: Literal[1]
    input_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    workspace_id: str = Field(min_length=1)
    revision: Literal[1]
    kind: PageKind
    page_index: int = Field(ge=0)
    items: list[dict] = Field(min_length=1,max_length=PAGE_SIZE)


class AnalysisManifest(Model):
    schema_version: Literal[1]
    kind: Literal['tempo-analysis-inputs']
    input_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    revision: Literal[1]
    workspace_id: str = Field(min_length=1)
    source: dict
    publication: dict
    publication_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    content_export_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    snapshot: dict
    activation: dict
    activation_origin: dict
    acceptance: dict
    current_serving: dict
    complete: bool
    readiness: Literal['ready','unknown']
    provenance: dict
    counts: dict[str,int]
    pages: list[PageReference] = Field(max_length=10000)
    manifest_digest: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def complete_manifest(self):
        document=self.model_dump(mode='json',exclude={'manifest_digest'})
        if digest(document)!=self.manifest_digest or set(self.counts)!=set(KINDS) or any(value<0 for value in self.counts.values()):
            raise ValueError('analysis_manifest_digest_or_counts_invalid')
        if not 1<=self.counts['definitions']<=2048 or not 1<=self.counts['games']<=100 or self.counts['comparisons']!=self.counts['games'] or self.counts['positions']<self.counts['games']:
            raise ValueError('analysis_manifest_scope_bound')
        identities=set()
        for kind in KINDS:
            references=[page for page in self.pages if page.kind==kind]
            if [page.page_index for page in references]!=list(range(len(references))) or sum(page.count for page in references)!=self.counts[kind]:
                raise ValueError('analysis_manifest_pages_incomplete')
            for page in references:
                identity=(kind,page.page_index)
                if identity in identities or page.filename!=f'{kind}-{page.page_index:06d}.json':
                    raise ValueError('analysis_manifest_duplicate_or_wrong_page')
                identities.add(identity)
        return self


def verify_directory(directory):
    directory=Path(directory).resolve()
    manifest_path=directory/'manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size>1024*1024:
        raise ValueError('analysis_manifest_file_bound')
    manifest=AnalysisManifest.model_validate_json(manifest_path.read_bytes())
    total=0
    for reference in manifest.pages:
        path=directory/reference.filename
        size=path.stat().st_size
        total+=size
        if path.is_symlink() or size>8*1024*1024 or total>64*1024*1024:
            raise ValueError('analysis_page_file_bound')
        page=AnalysisPage.model_validate_json(path.read_bytes())
        if (page.input_id,page.workspace_id,page.revision,page.kind,page.page_index,len(page.items))!=(manifest.input_id,manifest.workspace_id,manifest.revision,reference.kind,reference.page_index,reference.count) or digest(page.model_dump(mode='json'))!=reference.digest:
            raise ValueError('analysis_page_identity_or_digest_mismatch')
    return {'schema_version':1,'input_id':manifest.input_id,'manifest_digest':manifest.manifest_digest,'pages':len(manifest.pages),'counts':manifest.counts,'status':'verified'}
