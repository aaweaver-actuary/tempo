"""Study's semantic scoped suspend/restore boundary; no optional producer."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.study.conflicts import (
    Assessment, ConflictCommand, ConflictError, Eligibility, Occurrence, SourceFence, StudyConflicts,
)

NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)
FENCE=SourceFence('source-1',1,'digest-1')
SCOPE=Occurrence('repertoire-a','route-a',0,'shared-card',1)


class Authority:
    def __init__(self):
        self.fence=FENCE
        self.eligible=False
        self.reason='own_response_conflict'
    def assess(self, command):
        return Assessment(self.fence,tuple(Eligibility(item,self.eligible,self.reason) for item in command.occurrences))


class Store:
    def __init__(self):
        self.receipts={}
        self.holds={}
        self.queue={1:'queued',2:'queued',3:'blocked'}
        self.ours=set()
        self.unknown=()
        self.current=True
        self.mutations=0
        self.history=['actual retained Study review']
        self.schedule={'shared-card':'retained FSRS','unrelated-card':'untouched FSRS'}
    def lock(self, command): pass
    def fence_source(self, command): pass
    def receipt(self, identity): return self.receipts.get(identity)
    def validate_target(self, scope):
        if not self.current: raise ConflictError('current_physical_occurrence_unproved')
    def hold(self, scope, fence, active, now):
        self.mutations+=1
        self.holds[scope]=active
    def suspend(self, scope):
        if self.unknown: return (),self.unknown
        if self.queue[1]=='queued':
            self.queue[1]='blocked'
            self.ours.add(1)
            return (1,),()
        return (),()
    def restore(self, scope):
        if 1 in self.ours and self.queue[1]=='blocked':
            self.queue[1]='queued'
            return (1,)
        return ()
    def save_receipt(self, digest, receipt, now): self.receipts[receipt.command_id]=(digest,receipt)


@pytest.fixture
def owner():
    store,authority=Store(),Authority()
    return store,authority,StudyConflicts(store,authority,clock=lambda:NOW)


def command(identity='audit-1',**changes):
    return replace(ConflictCommand(identity,SCOPE.repertoire_id,FENCE,(SCOPE,)),**changes)


def test_scoped_conflict_suspends_without_lapse_and_preserves_unrelated_attempts(owner):
    store,_,service=owner
    receipt=service.synchronize(command())
    assert receipt.status=='applied' and receipt.suspended_queue_ids==(1,)
    assert store.queue=={1:'blocked',2:'queued',3:'blocked'}
    assert store.history==['actual retained Study review']
    assert store.schedule=={'shared-card':'retained FSRS','unrelated-card':'untouched FSRS'}


def test_resolved_current_occurrence_restores_only_own_suspension(owner):
    store,authority,service=owner
    service.synchronize(command())
    authority.eligible,authority.reason=True,None
    authority.fence=SourceFence('source-2',1,'digest-2')
    receipt=service.synchronize(command('resolved',expected=authority.fence))
    assert receipt.restored_queue_ids==(1,) and not receipt.blocked
    assert store.queue=={1:'queued',2:'queued',3:'blocked'}


def test_command_replay_is_idempotent_and_reused_identity_is_rejected(owner):
    store,_,service=owner
    first=service.synchronize(command())
    assert service.synchronize(command())==first and store.mutations==1
    with pytest.raises(ConflictError,match='command_identity_reused'):
        service.synchronize(command(expected=SourceFence('changed',2,'changed')))
    assert store.mutations==1


def test_conflict_policy_imports_without_infrastructure_or_optional_producers():
    import subprocess
    import sys
    script="""
import importlib.abc,sys
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname in {'psycopg','fastapi','app.main','app.tasks'} or fullname.startswith(('app.games','app.repertoire','app.product_composition')):
            raise AssertionError(fullname)
sys.meta_path.insert(0,RejectOptional())
from app.study.conflicts import StudyConflicts,SnapshotConflictAuthority
"""
    result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('unproved',['stale','unknown','target'])
def test_unproved_current_authority_cannot_block_or_restore(owner,unproved):
    store,authority,service=owner
    if unproved=='stale': authority.fence=SourceFence('changed',2,'changed')
    if unproved=='unknown': authority.reason='unpublished_integrity'
    if unproved=='target': store.current=False
    with pytest.raises(ConflictError): service.synchronize(command())
    assert store.mutations==0 and store.queue[1]=='queued' and not store.receipts


def test_ambiguous_shared_attempt_stays_untouched_and_reports_pending(owner):
    store,_,service=owner
    store.unknown=(1,)
    receipt=service.synchronize(command())
    assert receipt.status=='pending' and receipt.unproven_queue_ids==(1,)
    assert store.holds[SCOPE] and store.queue[1]=='queued' and store.queue[2]=='queued'


def test_resolution_does_not_override_later_burial(owner):
    store,authority,service=owner
    service.synchronize(command())
    store.queue[1]='buried'
    authority.eligible,authority.reason=True,None
    assert not service.synchronize(command('resolved')).restored_queue_ids
    assert store.queue[1]=='buried'


def test_snapshot_authority_uses_current_public_owner_policy_and_exact_binding():
    from types import SimpleNamespace
    from app.study.conflicts import SnapshotConflictAuthority
    snapshot=SimpleNamespace(membership=SimpleNamespace(source_revision=FENCE.source_revision,
        revision=FENCE.revision,digest=FENCE.digest))
    binding=SimpleNamespace(presentation_id=SCOPE.presentation_id,presentation_revision=1)
    route=SimpleNamespace(bindings=(binding,))
    calls=[]
    def current(repertoire):
        calls.append(('current',repertoire))
        return snapshot
    def index(value):
        assert value is snapshot
        calls.append(('index',))
        return SimpleNamespace(routes={(SCOPE.repertoire_id,SCOPE.route_id):(route,)})
    def policy(value,**inputs):
        assert value is snapshot
        calls.append(('policy',inputs))
        return SimpleNamespace(eligible=False,reason='own_response_conflict')
    authority=SnapshotConflictAuthority(current_snapshot=current,build_index=index,study_eligibility=policy)
    assessed=authority.assess(command())
    assert assessed==Assessment(FENCE,(Eligibility(SCOPE,False,'own_response_conflict'),))
    assert calls==[('current',SCOPE.repertoire_id),('index',),('policy',dict(
        repertoire_id=SCOPE.repertoire_id,route_id=SCOPE.route_id,ply=0,
        source_revision=FENCE.source_revision,revision=FENCE.revision,digest=FENCE.digest))]
    binding.presentation_revision=2
    with pytest.raises(ConflictError,match='current_physical_occurrence_unproved'):
        authority.assess(command())
