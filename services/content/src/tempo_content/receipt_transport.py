"""Explicit operator-only receipt verification; never called during startup."""
import json
import os
import hashlib
import re
from pathlib import Path
import stat
from urllib.parse import quote,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

from .domain import ContentError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*arguments,**keywords):
        return None


def authenticated_operation(operation, origin, token_file):
    endpoint=urlsplit(origin)
    if endpoint.scheme not in ('http','https') or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ('','/') or endpoint.scheme=='http' and endpoint.hostname not in ('127.0.0.1','localhost','::1'):
        raise ContentError('invalid_learning_origin','An explicit trusted Learning origin is required')
    token_file=Path(token_file)
    metadata=token_file.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.getuid() or stat.S_IMODE(metadata.st_mode)&0o077:
        raise ContentError('private_operator_credential_required','Receipt verification requires a private operator credential file')
    token=token_file.read_text().strip()
    if len(token)<32 or any(character.isspace() for character in token):
        raise ContentError('invalid_operator_credential','Operator credential is invalid')
    delivery=operation['document'].get('delivery')
    if delivery is None or delivery['endpoint']!=origin.rstrip('/'):
        raise ContentError('original_learning_authority_required','Reconcile through the original Learning authority')
    request=delivery['request']
    identifier=request.get('activation_id') or request['retirement_id']
    remote=Request(origin.rstrip('/')+'/internal/publications/operations/'+quote(identifier,safe=''),headers={'Authorization':'Bearer '+token})
    try:
        with build_opener(NoRedirect).open(remote,timeout=15) as response:
            raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:
                raise ValueError('receipt_size')
            receipt=json.loads(raw)
    except (OSError,ValueError) as error:
        raise ContentError('receipt_verification_unavailable','Retain the original operation and reconcile its authenticated Learning receipt') from None
    if not isinstance(receipt, dict) or receipt.get('operation_id') != identifier:
        raise ContentError('receipt_operation_mismatch', 'Learning receipt does not bind the original operation')
    return receipt


def verified_receipt(operation, proposed, origin, token_file):
    receipt = authenticated_operation(operation, origin, token_file)
    verify_operation_binding(operation, receipt)
    delivery = operation['document']['delivery']
    request = delivery['request']
    identifier = request.get('activation_id') or request['retirement_id']
    if receipt.get('state')!='complete':
        raise ContentError('activation_not_confirmed','Learning has not confirmed this original operation')
    acknowledgement=receipt['response']
    if delivery['retirement']:
        acknowledgement={'contract_version':1,'workspace_id':operation['draft']['source']['workspace_id'],
            'retirement_id':identifier,'publication_id':request['publication_id'],'publication_generation':request['publication_generation'],'reason':None,**acknowledgement}
    actual={'transport':'authenticated_learning_http','endpoint':origin.rstrip('/'),'acknowledgement':acknowledgement}
    if proposed!=actual:
        raise ContentError('receipt_transport_mismatch','Supplied bytes differ from the authenticated Learning receipt')
    return actual


def verify_operation_binding(operation, receipt):
    """Success and failure carry the same exact original request authority."""
    delivery = operation['document']['delivery']
    request = delivery['request']
    retirement = delivery['retirement']
    identifier = operation['id'] + (':retire' if retirement else ':activate')
    workspace = operation['draft']['source']['workspace_id']
    from .domain import stable_id
    publication_id = stable_id(workspace, operation['source_key'], 'publication', 'source')
    expected_generation = request['publication_generation'] if retirement else operation['document']['export']['publication']['publication_generation']
    if (receipt.get('operation_id') != identifier
            or receipt.get('workspace_id') != workspace
            or receipt.get('command_name') != ('publication.retire' if retirement else 'publication.activate.v2')
            or receipt.get('request') != request
            or request.get('publication_id') != publication_id
            or request.get('publication_generation') != expected_generation):
        raise ContentError('failure_receipt_mismatch', 'Learning receipt does not bind the original workspace/publication request')


def verified_terminal_failure(operation, origin, token_file):
    """Only the original authenticated immutable Learning failure is authority."""
    receipt = authenticated_operation(operation, origin, token_file)
    verify_operation_binding(operation, receipt)
    delivery = operation['document']['delivery']
    retirement = delivery['retirement']
    identifier = operation['id'] + (':retire' if retirement else ':activate')
    workspace = operation['draft']['source']['workspace_id']
    if receipt.get('state') != 'failed' or 'response' in receipt or not isinstance(receipt.get('error'), dict):
        raise ContentError('terminal_failure_not_confirmed', 'Learning has not confirmed an immutable terminal failure')
    error = receipt['error']
    # Never persist arbitrary remote strings: they can contain caller input.
    safe_error = {key: error[key] for key in ('status_code', 'retryable')
                  if (key == 'status_code' and type(error.get(key)) is int and 400 <= error[key] <= 599)
                  or (key == 'retryable' and type(error.get(key)) is bool)}
    if isinstance(error.get('code'), str) and re.fullmatch(r'[a-z][a-z0-9_]{0,99}', error['code']):
        safe_error['code'] = error['code']
    fingerprint = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {'transport': 'authenticated_learning_http', 'endpoint': origin.rstrip('/'),
            'content_operation_id': operation['id'], 'learning_operation_id': identifier,
            'workspace_id': workspace, 'state': 'failed', 'delivery': delivery,
            'error': safe_error, 'receipt_sha256': fingerprint}
