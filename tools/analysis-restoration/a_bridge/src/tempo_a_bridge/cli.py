"""Restartable Content -> authenticated Learning -> Game Evidence operator bridge."""
import argparse
import hashlib
from datetime import date
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class ContentCommandError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("content_command_failed:" + code)


class LearningHTTPError(ValueError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__('learning_http_' + str(status_code))


class ExecutionScopeError(ValueError):
    pass


def snapshot_identity(publication):
    identity = ["tempo-a-snapshot", 1, publication["workspace_id"],
                publication["publication_id"], publication["publication_generation"]]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "a-snapshot-v1:" + hashlib.sha256(encoded.encode()).hexdigest()


def observation_facts(snapshot):
    facts = json.loads(json.dumps(snapshot))
    for field in ("observed_at", "local_date"):
        facts["current_serving"].pop(field, None)
    return facts


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):
        return None


def execution_bundle(export, due_date):
    """Map the public serialized Content output, without importing its implementation."""
    definitions = export["publication"]["definitions"]
    facts = []
    routes_by_card = {}
    for route in export['routes']:
        for card_id in {binding['card_id'] for binding in route['bindings']}:
            routes_by_card.setdefault(card_id, []).append(route)
    for index, definition in enumerate(definitions):
        routes = routes_by_card.get(definition["card_id"], [])
        if not routes or definition["specification"]["type"] != "move_line":
            raise ExecutionScopeError("bridge_requires_bound_opening_move_lines")
        facts.append({"card_id": definition["card_id"], "content_type": "opening",
            "memberships": sorted({route["repertoire_id"] for route in routes}),
            "trained_color": routes[0]["learner_color"], "due_date": due_date, "route_order": index})
    routes = [{key: value for key,value in route.items() if key not in ("route_key","policy_key","repertoire_key")} for route in export["routes"]]
    return {"schema_version": 2, "publication": export["publication"], "source_digest": export["source"]["source_digest"],
        "facts": facts, "routes": routes, "legacy_aliases": export["legacy_aliases"]}


class Operator:
    def __init__(self, content_cli, endpoint, token_file):
        self.content_cli = str(content_cli)
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("explicit_learning_origin_required")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("remote_learning_requires_https")
        self.endpoint = endpoint.rstrip("/")
        token_file = Path(token_file).resolve()
        self.token_file=token_file
        metadata = token_file.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError("private_operator_credential_required")
        self.token = token_file.read_text().strip()
        if len(self.token) < 32 or any(character.isspace() for character in self.token):
            raise ValueError("invalid_operator_credential")

    def content(self, *arguments):
        environment = {key:value for key,value in os.environ.items() if not key.startswith(("PYTHON", "PG", "TEMPO_", "CELERY_")) or key in ("TEMPO_CONTENT_DATABASE_URL","TEMPO_CONTENT_WORKSPACE","TEMPO_CONTENT_RUNTIME_ROLE")}
        process = subprocess.run([self.content_cli,*map(str,arguments)],env=environment,capture_output=True,text=True)
        if process.returncode:
            try:
                code = json.loads(process.stderr).get("code", "unknown")
            except (ValueError, AttributeError):
                code = "unknown"
            if not isinstance(code, str) or not code.replace("_", "").isalnum():
                code = "unknown"
            raise ContentCommandError(code)
        return json.loads(process.stdout)

    def save(self, identifier, command, document):
        with tempfile.TemporaryDirectory(prefix="tempo-a-bridge-") as directory:
            path = Path(directory) / "document.json"
            path.write_text(json.dumps(document))
            path.chmod(0o600)
            arguments=['operation',command,identifier,'--file',path]
            if command=='record':
                arguments.extend(['--learning-origin',self.endpoint,'--operator-token-file',self.token_file])
            return self.content(*arguments)

    def http(self, path, document=None):
        request = Request(self.endpoint + path, data=json.dumps(document).encode() if document is not None else None,
            headers={"Authorization":"Bearer " + self.token,"Content-Type":"application/json"},method="POST" if document is not None else "GET")
        try:
            with build_opener(NoRedirect).open(request,timeout=15) as response:
                raw = response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:
                    raise ValueError("learning_response_byte_bound")
                return response.status,json.loads(raw)
        except HTTPError as error:
            raise LearningHTTPError(error.code) from None

    def publication_status(self, operation):
        publication_id = operation.get('publication_id') or operation['document'].get('export', {}).get('publication', {}).get('publication_id')
        if publication_id is None:
            publication_id = self.content('source', 'show', operation['source_key'])['publication_id']
        _, current = self.http('/internal/publications/' + quote(publication_id, safe='') + '/status')
        if (not isinstance(current, dict) or type(current.get('schema_version')) is not int or current['schema_version'] != 1
                or current.get('workspace_id') != operation['draft']['source']['workspace_id']
                or current.get('publication_id') != publication_id
                or current.get('status') not in ('active', 'retired', 'unavailable')
                or type(current.get('activation_revision')) is not int
                or current['activation_revision'] < 0):
            raise ValueError('invalid_learning_publication_status')
        try:
            if date.fromisoformat(current['local_date']).isoformat() != current['local_date']:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError('invalid_learning_publication_status') from None
        if ((current['status'] == 'unavailable' and (current.get('publication_generation') is not None or current['activation_revision'] != 0))
                or (current['status'] != 'unavailable' and (type(current.get('publication_generation')) is not int
                    or current['publication_generation'] < 1 or current['activation_revision'] < 1))):
            raise ValueError('invalid_learning_publication_status')
        return current

    def verify_operation_receipt(self, operation, receipt):
        delivery = operation['document']['delivery']
        identifier = operation['id'] + (':retire' if delivery['retirement'] else ':activate')
        if (not isinstance(receipt, dict) or receipt.get('operation_id') != identifier
                or (delivery['request'].get('retirement_id') if delivery['retirement'] else delivery['request'].get('activation_id')) != identifier
                or receipt.get('state') not in ('unknown', 'queued', 'complete', 'failed')):
            raise ValueError('invalid_learning_operation_receipt')
        if receipt['state'] == 'unknown':
            return
        try:
            same_request = json.dumps(receipt.get('request'), sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(delivery['request'], sort_keys=True, separators=(',', ':'), allow_nan=False)
        except (ValueError, TypeError):
            same_request = False
        if (receipt.get('workspace_id') != operation['draft']['source']['workspace_id']
                or receipt.get('command_name') != ('publication.retire' if delivery['retirement'] else 'publication.activate.v2')
                or not same_request):
            raise ValueError('learning_operation_authority_mismatch')

    def apply(self, identifier, max_slices=64):
        operation = self.content("operation","show",identifier)
        if operation["state"] in ("completed", "failed"):
            return operation
        if operation['state'] == 'cancelled':
            raise ValueError('operation_cancelled')
        current = None
        if not operation['document'].get('delivery'):
            if not operation['draft']['source']['exercises']:
                current = self.publication_status(operation)
                if current['status'] != 'active':
                    if operation['state'] == 'applying':
                        return self.content('operation', 'fail-before-delivery', identifier)
                    raise ValueError('retirement_requires_an_active_publication;operation_remains_approved_and_cancellable')
            try:
                operation = self.content("operation","prepare",identifier,"--max-slices",max_slices)
            except ContentCommandError:
                actual = self.content('operation', 'show', identifier)
                if actual['state'] == 'failed':
                    return actual
                raise
        if operation['state'] in ('completed', 'failed'):
            return operation
        if operation['state'] != 'applying':
            raise ValueError('application_not_authorized')
        prepared = operation["document"]
        if prepared.get("pending") and "export" not in prepared and not prepared.get("retirement_required"):
            return {"operation_id":identifier,"state":"pending_validation","inventory":self.content('inventory','status',operation['source_key'])}
        delivery = prepared.get("delivery")
        if delivery is None:
            current = current or self.publication_status(operation)
            publication_id = current['publication_id']
            retirement = bool(prepared.get("retirement_required"))
            if retirement and current["status"] != "active":
                return self.content('operation', 'fail-before-delivery', identifier)
            if not retirement:
                try:
                    execution_bundle(prepared['export'], current['local_date'])
                except ExecutionScopeError:
                    return self.content('operation', 'fail-before-delivery', identifier)
            request = {"publication_id":publication_id,"publication_generation":current["publication_generation"] if retirement else prepared["export"]["publication"]["publication_generation"],
                "expected_activation_revision":current["activation_revision"],
                "retirement_id" if retirement else "activation_id":identifier+(":retire" if retirement else ":activate")}
            delivery = {"endpoint":self.endpoint,"request":request,"retirement":retirement,"due_date":current['local_date']}
            try:
                operation = self.save(identifier,"checkpoint",{"delivery":delivery})
            except ContentCommandError as error:
                if error.code != 'checkpoint_conflict':
                    raise
                operation = self.content('operation', 'show', identifier)
            delivery = operation['document']['delivery']
        if delivery["endpoint"] != self.endpoint:
            raise ValueError("original_learning_authority_required")
        request = delivery["request"]
        remote_id = request.get("activation_id") or request["retirement_id"]
        _, receipt = self.http("/internal/publications/operations/"+quote(remote_id,safe=""))
        self.verify_operation_receipt(operation, receipt)
        if receipt["state"] == "failed":
            return self.record_failure(identifier)
        try:
            if receipt["state"] == "complete":
                acknowledgement = receipt["response"]
            elif receipt["state"] == "unknown":
                if not delivery["retirement"]:
                    try:
                        self.http("/internal/publications/v2/stage",execution_bundle(prepared["export"],delivery["due_date"]))
                    except LearningHTTPError as error:
                        if error.status_code not in (409, 413, 422):
                            raise
                        # A rejected response is only a trigger. Learning must
                        # serialize a durable terminal outcome before release.
                        return self.fence_and_reconcile(operation)
                code, acknowledgement = self.http("/internal/publications/retire" if delivery["retirement"] else "/internal/publications/v2/activate",request)
                if code == 202:
                    return {"operation_id":identifier,"state":"pending_activation","learning_operation_id":remote_id}
            elif receipt["state"] == "queued":
                code, acknowledgement = self.http("/internal/publications/operations/"+quote(remote_id,safe="")+"/retry",{})
                if code == 202:
                    return {"operation_id":identifier,"state":"pending_activation","learning_operation_id":remote_id}
            else:
                raise ValueError("invalid_learning_operation_state")
        except ValueError:
            # A rejected HTTP response alone is not a terminal outcome. Read the
            # original durable operation; Content independently verifies failure.
            _, recovered = self.http("/internal/publications/operations/"+quote(remote_id,safe=""))
            self.verify_operation_receipt(operation, recovered)
            if recovered.get("state") == "failed":
                return self.record_failure(identifier)
            if recovered.get("state") == "complete":
                acknowledgement = recovered["response"]
            else:
                raise
        return self.record_completion(operation, acknowledgement)

    def record_completion(self, operation, acknowledgement):
        identifier = operation['id']
        delivery = operation['document']['delivery']
        request = delivery['request']
        remote_id = request.get('activation_id') or request['retirement_id']
        if delivery["retirement"]:
            acknowledgement = {"contract_version":1,"workspace_id":operation["draft"]["source"]["workspace_id"],
                "retirement_id":remote_id,"publication_id":request["publication_id"],"publication_generation":request["publication_generation"],
                "reason":None,**acknowledgement}
        receipt = {"transport":"authenticated_learning_http","endpoint":self.endpoint,"acknowledgement":acknowledgement}
        return self.save(identifier,"record",receipt)

    def fence_and_reconcile(self, operation):
        delivery = operation['document']['delivery']
        if delivery['endpoint'] != self.endpoint:
            raise ValueError('original_learning_authority_required')
        request = delivery['request']
        remote_id = request.get('activation_id') or request['retirement_id']
        path = '/internal/publications/operations/' + quote(remote_id, safe='')
        _, receipt = self.http(path)
        self.verify_operation_receipt(operation, receipt)
        if receipt.get('state') not in ('complete', 'failed'):
            self.http(path + '/abort', {'workspace_id': operation['draft']['source']['workspace_id'],
                'command_name': 'publication.retire' if delivery['retirement'] else 'publication.activate.v2', 'request': request})
            _, receipt = self.http(path)
            self.verify_operation_receipt(operation, receipt)
        if receipt.get('state') == 'failed':
            return self.record_failure(operation['id'])
        if receipt.get('state') == 'complete':
            return self.record_completion(operation, receipt['response'])
        raise ValueError('learning_terminal_outcome_not_confirmed')

    def abandon(self, identifier):
        operation = self.content('operation', 'show', identifier)
        if operation['state'] in ('completed', 'failed'):
            return operation
        if operation['state'] != 'applying':
            raise ValueError('abandon_requires_applying;approved_operations_use_content_cancel')
        if not operation['document'].get('delivery'):
            try:
                return self.content('operation', 'fail-before-delivery', identifier)
            except ContentCommandError:
                # A checkpoint may have won the same Content scope lock.
                operation = self.content('operation', 'show', identifier)
                if operation['state'] in ('completed', 'failed'):
                    return operation
                if not operation['document'].get('delivery'):
                    raise
        return self.fence_and_reconcile(operation)

    def record_failure(self, identifier):
        return self.content("operation", "record-failure", identifier,
            "--learning-origin", self.endpoint, "--operator-token-file", self.token_file)

    def snapshot(self, identifier):
        operation = self.content("operation","show",identifier)
        if operation["state"] != "completed" or operation["document"].get("retirement_required"):
            raise ValueError("snapshot_requires_actual_completed_activation")
        prepared = operation["document"]
        if prepared["receipt"]["endpoint"] != self.endpoint:
            raise ValueError("original_learning_authority_required")
        export = prepared["export"]
        if export.get("export_profile") is not None or export.get("legal_replies_omitted"):
            raise ValueError("snapshot_requires_complete_legal_reply_inventory")
        _, current = self.http("/internal/publications/"+quote(export["publication"]["publication_id"],safe="")+"/status?generation="+str(export['publication']['publication_generation']))
        acceptance=current.pop('accepted')
        revision = current["activation_revision"]
        snapshot_id = snapshot_identity(export["publication"])
        key = snapshot_id + ":" + str(revision)
        if key in prepared.get("snapshots",{}):
            return prepared["snapshots"][key]
        snapshot = {"schema_version":2,"snapshot_id":snapshot_id,"revision":revision,
            "complete":export["complete"],"publication":export["publication"],"activation":prepared["receipt"]["acknowledgement"],
            "routes":[{key:value for key,value in route.items() if key not in ("route_key","policy_key","repertoire_key","repertoire_title")} for route in export["routes"]],
            "content_export":export,"current_serving":current,"activation_origin":{"transport":"authenticated_learning_http","endpoint":self.endpoint}}
        snapshot['acceptance']=acceptance
        try:
            operation = self.save(identifier,"checkpoint",{"snapshots":{key:snapshot}})
        except ContentCommandError as error:
            if error.code != "checkpoint_conflict":
                raise
            operation = self.content("operation", "show", identifier)
            winner = operation["document"].get("snapshots", {}).get(key)
            if winner is None or observation_facts(winner) != observation_facts(snapshot):
                raise
        return operation["document"]["snapshots"][key]


def main(arguments=None):
    parser = argparse.ArgumentParser(prog="tempo-a-bridge")
    parser.add_argument("--content-cli",type=Path,required=True)
    parser.add_argument("--learning-origin",required=True)
    parser.add_argument("--operator-token-file",type=Path,required=True)
    commands = parser.add_subparsers(dest="command",required=True)
    for name in ("apply","reconcile","snapshot","abandon"):
        command = commands.add_parser(name)
        command.add_argument("operation_id")
        if name == "snapshot":
            command.add_argument("--output",type=Path,required=True)
        elif name != 'abandon':
            command.add_argument("--max-slices",type=int,default=64)
    options = parser.parse_args(arguments)
    try:
        operator = Operator(options.content_cli,options.learning_origin,options.operator_token_file)
        if options.command == "snapshot":
            result = operator.snapshot(options.operation_id)
            encoded = json.dumps(result,sort_keys=True)+"\n"
            if options.output.exists():
                if options.output.read_text() != encoded:
                    raise ValueError("snapshot_output_conflict")
            else:
                with options.output.open("x") as output:
                    output.write(encoded)
        elif options.command == 'abandon':
            result = operator.abandon(options.operation_id)
        else:
            if not 1<=options.max_slices<=10000:
                raise ValueError("invalid_slice_bound")
            result = operator.apply(options.operation_id,options.max_slices)
        print(json.dumps(result,sort_keys=True,default=str))
        return 2 if result.get("state") == "failed" else 0
    except (ValueError,OSError,KeyError) as error:
        # Fixed operator errors only; URLs/credentials/driver exceptions are not printed.
        print(json.dumps({"status":"pending_or_failed","reason":str(error) if isinstance(error,ValueError) else "operator_unavailable:"+type(error).__name__}))
        return 2
