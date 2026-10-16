"""Explicit operator-only receipt verification; never called during startup."""
import json
import os
from pathlib import Path
import stat
from urllib.parse import quote,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

from .domain import ContentError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*arguments,**keywords):
        return None


def verified_receipt(operation, proposed, origin, token_file):
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
    if receipt.get('operation_id')!=identifier or receipt.get('state')!='complete':
        raise ContentError('activation_not_confirmed','Learning has not confirmed this original operation')
    acknowledgement=receipt['response']
    if delivery['retirement']:
        acknowledgement={'contract_version':1,'workspace_id':operation['draft']['source']['workspace_id'],
            'retirement_id':identifier,'publication_id':request['publication_id'],'publication_generation':request['publication_generation'],'reason':None,**acknowledgement}
    actual={'transport':'authenticated_learning_http','endpoint':origin.rstrip('/'),'acknowledgement':acknowledgement}
    if proposed!=actual:
        raise ContentError('receipt_transport_mismatch','Supplied bytes differ from the authenticated Learning receipt')
    return actual
