"""Named A import/proposal regressions run in regular installed consumers."""
import hashlib
import json
from copy import deepcopy

import chess
import pytest
from tempo_content.bridge import ImportManifest, ResponseProposal, map_import, digest
from tempo_content.domain import ContentError, canonical_source, stable_id
from tempo_content.domain import validate_exercise_step
from tempo_content.models import SourceExercise


def manifest(raw):
    return {"schema_version":1,"kind":"authored_import","workspace_id":"bridge-test","source_key":"opening",
        "title":"Original", "complete":True,"provenance":{"kind":"synthetic","source_sha256":hashlib.sha256(raw).hexdigest(),"description":"Explicit synthetic test"},
        "repertoires":[{"key":"white","title":"White","learner_color":"white"}],"policies":[{"key":"main","repertoire_key":"white"}],
        "route_assignments":[],"legacy_aliases":{},"reentry":[]}


def authored_pgn():
    return b'[Event "Title"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 *\n'


def test_A_accepted_alternatives_validate_from_the_same_starting_position():
    exercise=SourceExercise.model_validate({'key':'root','decision_key':'root','policy_key':'main','starting_fen':chess.STARTING_FEN,'prompt':'Play either response',
        'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['e2e4'],['d2d4']]}})
    assert validate_exercise_step(exercise) is None


def test_A_identity_rename_permutation_uses_stable_policy_decisions():
    raw = authored_pgn()
    description = manifest(raw)
    description["route_assignments"]=[{"fingerprint":digest([chess.STARTING_FEN,["e2e4","e7e5","g1f3"]]),"route_key":"route","policy_key":"main"}]
    source,_ = map_import(raw,description)
    renamed = raw.replace(b'Title',b'Renamed')
    successor = deepcopy(description)
    successor["title"]="Renamed source"
    successor["provenance"]["source_sha256"]=hashlib.sha256(renamed).hexdigest()
    other,_ = map_import(renamed,successor)
    assert source.exercises == other.exercises
    assert [stable_id(source.workspace_id,source.source_key,"card",item.key) for item in source.exercises] == [stable_id(other.workspace_id,other.source_key,"card",item.key) for item in other.exercises]
    assert len(source.exercises)==2


def test_A_import_requires_provenance_color_and_exact_declared_scope():
    raw = authored_pgn()
    description = manifest(raw)
    with pytest.raises(ContentError,match="explicit stable"):
        map_import(raw,description)
    description["provenance"]["source_sha256"]="0"*64
    with pytest.raises(ContentError,match="Input bytes"):
        map_import(raw,description)
    del description["repertoires"][0]["learner_color"]
    with pytest.raises(ValueError):
        ImportManifest.model_validate(description)


def test_A_proposal_is_not_approval_and_requires_legal_executable_content():
    proposal={"schema_version":1,"kind":"response_proposal","status":"draft","proposal_id":"one",
        "workspace_id":"bridge-test","source_key":"opening","repertoire_key":"white","policy_key":"main","learner_color":"white",
        "expected_source_revision":1,"source_digest":"0"*64,"route_key":"route","anchor_ply":1,"opponent_move":"c7c5",
        "decision_fen":"rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",
        "accepted_lines":[["g1f3"]],"provenance":{"kind":"synthetic","evidence_refs":["game:1"]}}
    assert ResponseProposal.model_validate(proposal).status=="draft"
    for mutation in ({"kind":"gap_context"},{"status":"approved"},{"accepted_lines":[]},{"accepted_lines":[["g1g5"]]},
                     {'provenance':{}},{'provenance':{'kind':'synthetic','evidence_refs':[]}}):
        with pytest.raises(ValueError):
            ResponseProposal.model_validate({**proposal,**mutation})


@pytest.mark.parametrize('retirement', [False, True])
@pytest.mark.parametrize('state', ['unknown', 'queued', 'complete', 'failed'])
@pytest.mark.parametrize('mismatch', [None, 'workspace_id', 'command_name', 'request', 'operation_id'])
def test_A_terminal_failure_requires_original_authenticated_request(monkeypatch, tmp_path, state, mismatch, retirement):
    from tempo_content import receipt_transport
    workspace, source_key = 'bridge-test', 'opening'
    publication_id = stable_id(workspace, source_key, 'publication', 'source')
    request = {'activation_id': 'original:activate', 'publication_id': publication_id, 'publication_generation': 2, 'expected_activation_revision': 1}
    remote_id = 'original:retire' if retirement else 'original:activate'
    if retirement:
        request['retirement_id'] = request.pop('activation_id').replace(':activate', ':retire')
    operation = {'id': 'original', 'source_key': source_key, 'draft': {'source': {'workspace_id': workspace}},
        'document': {'delivery': {'endpoint': 'http://127.0.0.1:1', 'retirement': retirement, 'request': request},
                     'export': {'publication': {'publication_generation': 2}}}}
    receipt = {'operation_id': remote_id, 'state': state, 'workspace_id': workspace,
               'command_name': 'publication.retire' if retirement else 'publication.activate.v2', 'request': request, 'error': {'status_code': 409, 'detail': 'secret remote data'}}
    if mismatch:
        receipt[mismatch] = {'publication_id': 'wrong'} if mismatch == 'request' else 'wrong'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, maximum): return json.dumps(receipt).encode()
    class Opener:
        def open(self, request, timeout):
            assert request.get_header('Authorization') == 'Bearer ' + 'a' * 40
            return Response()
    monkeypatch.setattr(receipt_transport, 'build_opener', lambda *args: Opener())
    credential = tmp_path / 'token'
    credential.write_text('a' * 40)
    credential.chmod(0o600)
    with pytest.raises(ContentError, match='original Learning authority'):
        receipt_transport.verified_terminal_failure(operation, 'http://127.0.0.1:2', credential)
    if state == 'failed' and mismatch is None:
        failure = receipt_transport.verified_terminal_failure(operation, 'http://127.0.0.1:1', credential)
        assert failure['error'] == {'status_code': 409}
        assert 'secret remote data' not in json.dumps(failure)
    else:
        with pytest.raises(ContentError):
            receipt_transport.verified_terminal_failure(operation, 'http://127.0.0.1:1', credential)


@pytest.mark.parametrize('failure', ['redirect', 'oversized', 'malformed', 'unauthenticated', 'unavailable'])
def test_A_failure_transport_rejects_uncertain_http_outcomes(tmp_path, failure):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from tempo_content.receipt_transport import authenticated_operation
    redirected = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect-target':
                redirected.append(True)
            code = {'redirect':302, 'unauthenticated':401, 'unavailable':503}.get(failure, 200)
            self.send_response(code)
            if failure == 'redirect':
                self.send_header('Location', '/redirect-target')
            self.end_headers()
            if failure == 'oversized':
                self.wfile.write(b' ' * (1024 * 1024 + 1))
            elif failure == 'malformed':
                self.wfile.write(b'{not json')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    token = tmp_path / 'token'
    token.write_text('a' * 40); token.chmod(0o600)
    origin = 'http://127.0.0.1:' + str(server.server_port)
    operation = {'document': {'delivery': {'endpoint': origin, 'request': {'activation_id':'original:activate'}}}}
    try:
        with pytest.raises(ContentError, match='Retain the original operation'):
            authenticated_operation(operation, origin, token)
        assert not redirected
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=3)


@pytest.mark.parametrize('fault', ['explanation', 'engine', 'unbound', 'terminal', 'alias_merge'])
def test_A_bridge_scope_preflight_is_local_and_preserves_general_types(fault):
    from tempo_content.bridge import validate_bridge_execution_scope
    from tempo_content.models import SourceSnapshot
    raw = authored_pgn()
    description = manifest(raw)
    description['complete'] = True
    description['route_assignments'] = [{'fingerprint': digest([chess.STARTING_FEN, ['e2e4', 'e7e5', 'g1f3']]), 'route_key': 'route', 'policy_key': 'main'}]
    source, parsed = map_import(raw, description)
    document = source.model_dump(mode='json')
    description = parsed.model_dump(mode='json')
    if fault == 'explanation': document['exercises'][0]['specification'] = {'type': 'explanation', 'rubric': 'Explain central control'}
    elif fault == 'engine': document['exercises'][0]['specification']['grading_policy'] = 'open_judgment'
    elif fault == 'unbound': document['bindings'] = [binding for binding in document['bindings'] if binding['exercise_key'] != document['exercises'][0]['key']]
    elif fault == 'terminal':
        document['bindings'][0]['ply'] = len(document['routes'][0]['moves'])
    else: description['legacy_aliases'] = {exercise['key']: 'same-physical-card' for exercise in document['exercises']}
    valid_source = SourceSnapshot.model_validate(document)
    with pytest.raises(ContentError) as rejected:
        validate_bridge_execution_scope(valid_source, description)
    assert rejected.value.code == 'bridge_scope_unsupported'


@pytest.mark.parametrize('retirement', [False, True])
@pytest.mark.parametrize('mismatch', [None, 'workspace_id', 'command_name', 'request', 'operation_id'])
def test_A_completion_receipt_requires_exact_original_authority(monkeypatch, retirement, mismatch):
    from tempo_content import receipt_transport
    workspace, source_key = 'bridge-test', 'opening'
    publication_id = stable_id(workspace, source_key, 'publication', 'source')
    remote_id = 'original:retire' if retirement else 'original:activate'
    request = {'retirement_id' if retirement else 'activation_id': remote_id, 'publication_id': publication_id,
        'publication_generation': 2, 'expected_activation_revision': 1}
    delivery = {'endpoint': 'http://127.0.0.1:1', 'retirement': retirement, 'request': request}
    operation = {'id': 'original', 'source_key': source_key, 'draft': {'source': {'workspace_id': workspace}},
        'document': {'delivery': delivery, 'export': {'publication': {'publication_generation': 2}}}}
    response = {'status': 'retired', 'stale_attempts_fenced': True, 'activation_revision': 2} if retirement else {
        'contract_version': 1, 'workspace_id': workspace, 'publication_id': publication_id, 'publication_generation': 2,
        'activation_id': remote_id, 'status': 'activated', 'activation_revision': 2}
    acknowledgement = {'contract_version': 1, 'workspace_id': workspace, 'retirement_id': remote_id,
        'publication_id': publication_id, 'publication_generation': 2, 'reason': None, **response} if retirement else response
    proposed = {'transport': 'authenticated_learning_http', 'endpoint': delivery['endpoint'], 'acknowledgement': acknowledgement}
    receipt = {'operation_id': remote_id, 'workspace_id': workspace, 'command_name': 'publication.retire' if retirement else 'publication.activate.v2',
        'request': deepcopy(request), 'state': 'complete', 'response': response}
    if mismatch:
        receipt[mismatch] = {**request, 'publication_id': 'another-publication'} if mismatch == 'request' else 'wrong'
    monkeypatch.setattr(receipt_transport, 'authenticated_operation', lambda *args: receipt)
    if mismatch:
        with pytest.raises(ContentError):
            receipt_transport.verified_receipt(operation, proposed, delivery['endpoint'], '/unused/token')
    else:
        assert receipt_transport.verified_receipt(operation, proposed, delivery['endpoint'], '/unused/token') == proposed
