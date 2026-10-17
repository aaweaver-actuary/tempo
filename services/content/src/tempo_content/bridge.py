"""Content-owned immutable drafts, durable approval, and serialized bridge output.

Only the external operator delivers to Learning. This module has no network or
foreign database access. Direct source writers share the approval scope lock.
"""
from datetime import datetime, timezone
import hashlib
import base64
import zlib
import io
import json
from typing import Literal

import chess
import chess.pgn
from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from .domain import ContentError, canonical_source, canonical_position, stable_id
from .models import SourceModel, SourceSnapshot, SourceRepertoire, SourcePolicy


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Provenance(SourceModel):
    kind: Literal["synthetic", "authored_export", "public_reference"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class RouteAssignment(SourceModel):
    fingerprint: str
    route_key: str
    policy_key: str


class Reentry(SourceModel):
    route_key: str
    policy_key: str
    ply: int = Field(ge=0, le=1000)


class ImportManifest(SourceModel):
    schema_version: Literal[1]
    kind: Literal["authored_import"]
    workspace_id: str
    source_key: str
    title: str
    complete: bool
    provenance: Provenance
    repertoires: list[SourceRepertoire] = Field(default_factory=list)
    policies: list[SourcePolicy] = Field(default_factory=list)
    route_assignments: list[RouteAssignment] = Field(default_factory=list, max_length=10000)
    legacy_aliases: dict[str, str] = Field(default_factory=dict, max_length=8192)
    historical_mapping_unknown: list[str] = Field(default_factory=list, max_length=8192)
    reentry: list[Reentry] = Field(default_factory=list, max_length=10000)


class ResponseProposal(SourceModel):
    schema_version: Literal[1]
    kind: Literal["response_proposal"]
    status: Literal["draft"]
    proposal_id: str = Field(min_length=1, max_length=200)
    workspace_id: str
    source_key: str
    repertoire_key: str
    policy_key: str
    learner_color: Literal["white", "black"]
    expected_source_revision: int = Field(ge=1)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_key: str
    anchor_ply: int = Field(ge=0, le=999)
    opponent_move: str
    decision_fen: str
    accepted_lines: list[list[str]] = Field(min_length=1, max_length=20)
    provenance: dict

    @model_validator(mode="after")
    def legal_lines(self):
        references = self.provenance.get('evidence_refs')
        if (not isinstance(self.provenance.get('kind'),str) or not self.provenance['kind'].strip()
                or not isinstance(references,list) or not references or len(references)>100
                or any(not isinstance(reference,str) or not reference.strip() for reference in references)):
            raise ValueError('proposal_provenance_and_evidence_references_required')
        board = chess.Board(self.decision_fen)
        if not board.is_valid() or board.turn != (self.learner_color == "white"):
            raise ValueError("proposal_decision_color_or_fen_invalid")
        for line in self.accepted_lines:
            if not 1 <= len(line) <= 40:
                raise ValueError("proposal_line_bound")
            position = board.copy()
            for move in line:
                position.push_uci(move)
            if position.turn == board.turn:
                raise ValueError("proposal_must_end_on_learner_move")
        return self


def map_import(raw, manifest):
    manifest = ImportManifest.model_validate(manifest)
    if hashlib.sha256(raw).hexdigest() != manifest.provenance.source_sha256:
        raise ContentError("source_digest_mismatch", "Input bytes differ from declared provenance")
    if raw.lstrip().startswith(b"{"):
        source = SourceSnapshot.model_validate_json(raw)
    else:
        assignments = {item.fingerprint: item for item in manifest.route_assignments}
        if len(assignments) != len(manifest.route_assignments):
            raise ContentError("ambiguous_route_mapping", "Repeated route fingerprint")
        policies = {item.key: item for item in manifest.policies}
        repertoires = {item.key: item for item in manifest.repertoires}
        routes, memberships, exercises, bindings, seen = {}, [], {}, [], set()
        stream = io.StringIO(raw.decode("utf-8-sig"))
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors or game.headers.get("Variant", "Standard").lower() not in ("standard", "from position"):
                raise ContentError("invalid_pgn", "Only legal standard/from-position authored PGN is supported")
            board = game.board()
            # All variations are authored accepted lines; titles and record order are not identities.
            leaves = []
            pending = [(game,[])]
            while pending:
                node,moves=pending.pop()
                if len(moves) > 1000:
                    raise ContentError("route_bound", "Authored route exceeds 1000 plies")
                if not node.variations:
                    leaves.append(moves)
                for child in node.variations:
                    pending.append((child,moves+[child.move.uci()]))
                if len(leaves)+len(pending)>10000:
                    raise ContentError('route_count_bound','Authored PGN exceeds 10,000 complete paths')
            for moves in leaves:
                fingerprint = digest([board.fen(en_passant="fen"), moves])
                assignment = assignments.get(fingerprint)
                if assignment is None or assignment.policy_key not in policies:
                    raise ContentError("route_mapping_missing", "Each legal PGN path requires an explicit stable policy/route assignment")
                policy = policies[assignment.policy_key]
                repertoire = repertoires[policy.repertoire_key]
                route = {"key": assignment.route_key, "starting_fen": board.fen(en_passant="fen"), "moves": moves}
                if route["key"] in routes and routes[route["key"]] != route:
                    raise ContentError("route_identity_conflict", "One route key identifies different paths")
                routes[route["key"]] = route
                member = {"repertoire_key": repertoire.key, "policy_key": policy.key, "route_key": route["key"]}
                if member in memberships:
                    continue
                memberships.append(member)
                seen.add(fingerprint)
                position = board.copy()
                for ply, move in enumerate(moves):
                    if position.turn == (repertoire.learner_color == "white"):
                        key = digest([policy.key, position.fen(en_passant="fen")])
                        exercise = exercises.setdefault(key, {"key": key, "decision_key": key, "policy_key": policy.key,
                            "starting_fen": position.fen(en_passant="fen"), "prompt": "Play your repertoire response",
                            "specification": {"type": "move_line", "mode": "single", "grading_policy": "reference", "accepted_lines": []}})
                        if [move] not in exercise["specification"]["accepted_lines"]:
                            exercise["specification"]["accepted_lines"].append([move])
                            exercise["specification"]["accepted_lines"].sort()
                        bindings.append({"route_key": route["key"], "policy_key": policy.key, "ply": ply, "exercise_key": key})
                    position.push_uci(move)
        if seen != set(assignments) or not routes:
            raise ContentError("source_scope_mismatch", "Manifest paths must exactly cover the declared PGN scope")
        source = SourceSnapshot.model_validate({"schema_version": 1, "workspace_id": manifest.workspace_id,
            "source_key": manifest.source_key, "title": manifest.title,
            "repertoires": [item.model_dump() for item in manifest.repertoires],
            "policies": [item.model_dump() for item in manifest.policies], "routes": list(routes.values()),
            "memberships": memberships, "exercises": list(exercises.values()), "bindings": bindings})
    if (source.workspace_id, source.source_key) != (manifest.workspace_id, manifest.source_key):
        raise ContentError("source_identity_mismatch", "Manifest and structured source identities differ")
    if not set(manifest.legacy_aliases) <= {item.key for item in source.exercises}:
        raise ContentError("alias_target_missing", "Historical alias targets an unknown exercise")
    if not set(manifest.historical_mapping_unknown) <= {item.key for item in source.exercises}:
        raise ContentError('unknown_history_target_missing', 'Unknown history must identify an actual exercise')
    if any(not any((binding.route_key, binding.policy_key, binding.ply) == (entry.route_key, entry.policy_key, entry.ply)
                   for binding in source.bindings) for entry in manifest.reentry):
        raise ContentError("reentry_binding_missing", "Explicit re-entry requires an actual binding")
    return source, manifest


def lock_scope(database, source_id, operation_id=None):
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("content:scope:" + source_id,))
    reservation = database.execute("SELECT id FROM content.bridge_operations WHERE source_id=%s AND state IN ('approved','applying')", (source_id,)).fetchone()
    if reservation and reservation["id"] != operation_id:
        raise ContentError("source_reserved", "An approved operation owns this source; inspect/reconcile it first")


def validate_bridge_execution_scope(source, manifest):
    """Cheap A-only compatibility checks; general Content v1 remains unchanged.

    Route traversal, chess legality and inventory construction retain their
    bounded worker owner. This checks declared execution scope, not readiness.
    """
    source = SourceSnapshot.model_validate(source)
    manifest = ImportManifest.model_validate(manifest)
    if not manifest.complete:
        raise ContentError('bridge_scope_unsupported', 'A requires a complete source')
    if len(source.exercises) > 8192:
        raise ContentError('bridge_scope_unsupported', 'A supports at most 8192 complete opening definitions')
    exercises = {exercise.key: exercise for exercise in source.exercises}
    if any(exercise.specification.type != 'move_line' or exercise.specification.grading_policy != 'reference'
           for exercise in exercises.values()):
        raise ContentError('bridge_scope_unsupported', 'A requires reference-graded opening move lines; general Content v1 supports other exercise types')
    routes = {route.key: route for route in source.routes}
    covered = set()
    for binding in source.bindings:
        if binding.ply >= len(routes[binding.route_key].moves):
            raise ContentError('bridge_scope_unsupported', 'A bindings require an executable move within an authored route')
        covered.add(binding.exercise_key)
    if covered != exercises.keys():
        raise ContentError('bridge_scope_unsupported', 'Every A definition requires an actual policy/repertoire route binding')
    # SourceSnapshot already verifies policy ownership, membership, unique
    # bindings and repertoire/color declarations. Preserve its general model.
    aliases = manifest.legacy_aliases
    identities = [aliases.get(key, stable_id(source.workspace_id, source.source_key, 'card', key)) for key in exercises]
    if not aliases.keys() <= exercises.keys() or len(set(identities)) != len(identities):
        raise ContentError('bridge_scope_unsupported', 'A historical aliases must identify distinct executable objects')


def append_export_scope(inventory, scope, budget):
    """Reject expansion before retaining another potentially large response scope."""
    additional = len(canonical(scope).encode()) + bool(inventory['scopes'])
    if budget['bytes'] + additional > budget['maximum']:
        raise ContentError('bridge_export_limit', 'Complete bridge export exceeds 64 MiB; no Learning activation was attempted')
    inventory['scopes'].append(scope)
    budget['bytes'] += additional


MAX_BRIDGE_DOCUMENT_BYTES = 64 * 1024 * 1024


def pack_bridge_document(document):
    raw=canonical(document).encode()
    if len(raw)>MAX_BRIDGE_DOCUMENT_BYTES:
        raise ContentError('bridge_document_limit','Bridge document exceeds 64 MiB')
    return {'encoding':'zlib-base64-v1','sha256':hashlib.sha256(raw).hexdigest(),
            'data':base64.b64encode(zlib.compress(raw)).decode('ascii')}


def unpack_bridge_document(packed):
    try:
        if packed['encoding']!='zlib-base64-v1' or len(packed['data'])>2*MAX_BRIDGE_DOCUMENT_BYTES:
            raise ValueError()
        compressed=base64.b64decode(packed['data'],validate=True)
        decoder=zlib.decompressobj()
        raw=decoder.decompress(compressed,MAX_BRIDGE_DOCUMENT_BYTES+1)
        if len(raw)>MAX_BRIDGE_DOCUMENT_BYTES or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest()!=packed['sha256']:
            raise ValueError()
        return json.loads(raw)
    except (ValueError,KeyError,TypeError,zlib.error):
        raise ContentError('bridge_document_corrupt','Immutable bridge document is unavailable or corrupt') from None


class Bridge:
    def __init__(self, service):
        self.service = service

    def draft(self, source, manifest, expected_revision, proposal=None):
        source = SourceSnapshot.model_validate(source)
        manifest = ImportManifest.model_validate(manifest)
        if source.workspace_id != self.service.workspace_id or source.workspace_id != manifest.workspace_id or source.source_key != manifest.source_key:
            raise ContentError("workspace_mismatch", "Draft belongs to a different source/workspace")
        if len(source.exercises)>8192:
            raise ContentError('bridge_definition_limit','This bridge supports complete sources of at most 8192 definitions; no truncation is performed')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ContentError("invalid_source_revision", "Expected revision must be a nonnegative integer")
        # Freeze canonical ordering before identity; provenance byte digest remains explicit.
        document = {"schema_version": 1, "source": json.loads(canonical_source(source)),
            "manifest": manifest.model_dump(mode="json"), "expected_revision": expected_revision, "proposal": proposal}
        for collection in ('repertoires','policies','route_assignments','reentry'):
            document['manifest'][collection]=sorted(document['manifest'][collection],key=canonical)
        fingerprint = digest(document)
        semantic = json.loads(canonical(document))
        semantic['manifest']['provenance']['source_sha256'] = digest(semantic['source'])
        identifier = stable_id(source.workspace_id, source.source_key, "draft", digest(semantic))
        source_id = stable_id(source.workspace_id, source.source_key, "source", source.source_key)
        with self.service._transaction() as database:
            database.execute("INSERT INTO content.bridge_drafts VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identifier, source_id, source.source_key, expected_revision, fingerprint, Jsonb(document)))
        # A conflict may be the primary key or the logical proposal identity.
        # Read after the INSERT transaction: a concurrent winner is now durable.
        if proposal is not None:
            previous = self.service._one("SELECT * FROM content.bridge_drafts WHERE document->'proposal'->>'proposal_id'=%s AND document->'proposal'->>'workspace_id'=%s AND source_key=%s",
                (proposal["proposal_id"], proposal["workspace_id"], source.source_key))
            if previous is not None and previous["document"]["proposal"] != proposal:
                raise ContentError("proposal_identity_conflict", "Proposal idempotency identity was reused with different contents")
        else:
            previous = self.service._one("SELECT * FROM content.bridge_drafts WHERE id=%s", (identifier,))
        if previous is None:
            raise ContentError("draft_not_found", "Draft is unavailable")
        if previous["digest"] != fingerprint or digest(previous["document"]) != fingerprint:
            raise ContentError("immutable_draft_identity_conflict", "An immutable draft identity already has different content or provenance")
        return {**previous, "status": "draft"}

    def draft_show(self, identifier):
        row = self.service._one("SELECT * FROM content.bridge_drafts WHERE id=%s", (identifier,))
        if not row:
            raise ContentError("draft_not_found", "Draft is unavailable")
        return {**row, "status": "draft"}

    def approve(self, identifier, fingerprint):
        draft = self.draft_show(identifier)
        if draft["digest"] != fingerprint:
            raise ContentError("draft_digest_mismatch", "Approval must bind the exact inspected draft digest")
        if not draft["document"]["manifest"]["complete"]:
            raise ContentError("incomplete_source", "Incomplete authored scope cannot replace serving content")
        operation_id = stable_id(self.service.workspace_id, draft["source_key"], "operation", identifier)
        with self.service._transaction() as database:
            # Consistent scope-before-operation/source ordering, including initial source creation.
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("content:scope:" + draft["source_id"],))
            previous = database.execute("SELECT id FROM content.bridge_operations WHERE draft_id=%s", (identifier,)).fetchone()
            if not previous:
                lock_scope(database, draft["source_id"])
                current = database.execute("SELECT requested_revision FROM content.sources WHERE id=%s", (draft["source_id"],)).fetchone()
                if (current["requested_revision"] if current else 0) != draft["expected_revision"]:
                    raise ContentError("stale_source_revision", "Draft was based on an obsolete source revision")
                database.execute("INSERT INTO content.bridge_operations(id,draft_id,source_id,state) VALUES(%s,%s,%s,'approved')",
                    (operation_id, identifier, draft["source_id"]))
        return self.operation(operation_id)

    def operation(self, identifier):
        row = self.service._one("SELECT o.*,d.digest,d.source_key,d.expected_revision,d.document AS draft FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.id=%s", (identifier,))
        if not row:
            raise ContentError("operation_not_found", "No durable approved operation exists")
        document=dict(row['document'])
        if 'export_packed' in document:
            document['export']=unpack_bridge_document(document.pop('export_packed'))
            document.pop('export_identity',None)
        if 'snapshots_packed' in document:
            document['snapshots']={**document.get('snapshots',{}),**{key:unpack_bridge_document(value) for key,value in document.pop('snapshots_packed').items()}}
        return {**row, 'document':document, 'publication_id': stable_id(self.service.workspace_id, row['source_key'], 'publication', 'source')}

    def _fail_before_delivery(self, identifier, failure, *, approved=False):
        operation = self.operation(identifier)
        with self.service._transaction() as database:
            database.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('content:scope:' + operation['source_id'],))
            row = database.execute('SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            if row['state'] == 'failed':
                if row['document'].get('failure') != failure:
                    raise ContentError('terminal_failure_conflict', 'Terminal failure is immutable')
            elif row['state'] in (('approved', 'applying') if approved else ('applying',)) and 'delivery' not in row['document'] and 'receipt' not in row['document']:
                lock_scope(database, operation['source_id'], identifier)
                database.execute("UPDATE content.bridge_operations SET state='failed',document=document || %s,updated_at=now() WHERE id=%s", (Jsonb({'failure': failure}), identifier))
            else:
                raise ContentError('pre_delivery_failure_not_authorized', 'Only a proven undelivered application can fail locally; reconcile checkpointed Learning authority')
        return self.operation(identifier)

    def fail_before_delivery(self, identifier):
        """Supported operator abandonment; checkpoint and admission use this lock."""
        return self._fail_before_delivery(identifier, {'code': 'operator_abandoned_before_delivery'})

    def cancel(self, identifier):
        operation = self.operation(identifier)
        with self.service._transaction() as database:
            lock_scope(database, operation["source_id"], identifier)
            row = database.execute("SELECT state FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if row["state"] == "approved":
                database.execute("UPDATE content.bridge_operations SET state='cancelled',updated_at=now() WHERE id=%s", (identifier,))
            elif row["state"] != "cancelled":
                return {"operation_id": identifier, "cancelled": False, "reason": "application_started"}
        return {"operation_id": identifier, "cancelled": True}

    def prepare(self, identifier, max_slices=64):
        try:
            return self._prepare(identifier,max_slices)
        except ContentError as error:
            if error.code in ('inventory_failed','bridge_inventory_limit','bridge_export_limit','bridge_document_limit'):
                operation=self.operation(identifier)
                with self.service._transaction() as database:
                    lock_scope(database,operation['source_id'],identifier)
                    row=database.execute('SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE',(identifier,)).fetchone()
                    if row['state']=='applying' and not row['document'].get('delivery') and not row['document'].get('export') and not row['document'].get('export_packed'):
                        database.execute("UPDATE content.bridge_operations SET state='failed',document=document || %s,updated_at=now() WHERE id=%s",(Jsonb({'failure':{'code':error.code,'message':error.message}}),identifier))
            raise

    def _prepare(self, identifier, max_slices):
        operation = self.operation(identifier)
        if operation['state'] not in ('completed', 'failed', 'cancelled'):
            try:
                validate_bridge_execution_scope(operation['draft']['source'], operation['draft']['manifest'])
            except ContentError as error:
                self._fail_before_delivery(identifier, {'code': error.code, 'message': error.message}, approved=True)
                raise
        with self.service._transaction() as database:
            lock_scope(database, operation["source_id"], identifier)
            row = database.execute("SELECT state FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if row["state"] == "cancelled":
                raise ContentError("operation_cancelled", "Cancelled intent cannot authorize application")
            if row['state']=='failed':
                raise ContentError('operation_failed','Failed pre-delivery intent cannot authorize application; inspect its failure and create a corrected draft')
            if row["state"] == "completed":
                return self.operation(identifier)
            database.execute("UPDATE content.bridge_operations SET state='applying',updated_at=now() WHERE id=%s", (identifier,))
        draft = operation["draft"]
        loaded = self.service.load_source(draft["source"], draft["expected_revision"], operation_id=identifier)
        for _ in range(max_slices):
            if self.service.inventory_status(operation["source_key"])["state"] in ("complete", "failed", "retirement_required"):
                break
            self.service.work_once(operation["source_key"])
        source = SourceSnapshot.model_validate(draft["source"])
        status = self.service.inventory_status(source.source_key)
        if status['state']=='failed':
            raise ContentError('inventory_failed',canonical(status['error']))
        if not source.exercises:
            prepared = {"retirement_required": True, **loaded}
        elif self.service.inventory_status(source.source_key)["state"] == "complete":
            publication = self.service.publication_build(source.source_key)
            export = self.export(publication.publication_id, publication.publication_generation, draft["manifest"])
            prepared = {"export_packed": pack_bridge_document(export),
                "export_identity": {key: export["publication"][key] for key in ("publication_id", "publication_generation")}}
        else:
            prepared = {"pending": True, **loaded}
        with self.service._transaction() as database:
            database.execute("UPDATE content.bridge_operations SET document=document || %s,updated_at=now() WHERE id=%s AND state='applying'", (Jsonb(prepared), identifier))
        return self.operation(identifier)

    def record(self, identifier, receipt):
        # This is a trusted local operator port, not an unauthenticated network receiver.
        from tempo_content_publication_contracts.v1 import ActivationAcknowledgement, RetirementAcknowledgement
        operation = self.operation(identifier)
        retirement = operation["document"].get("retirement_required", False)
        acknowledgement = (RetirementAcknowledgement if retirement else ActivationAcknowledgement).model_validate(receipt["acknowledgement"])
        if acknowledgement.workspace_id != self.service.workspace_id:
            raise ContentError("receipt_workspace_mismatch", "Activation receipt belongs to another workspace")
        expected_id = stable_id(self.service.workspace_id, operation["source_key"], "publication", "source")
        if acknowledgement.publication_id != expected_id or acknowledgement.status != ("retired" if retirement else "activated"):
            raise ContentError("receipt_publication_mismatch", "Receipt does not confirm this operation's publication")
        expected_operation = identifier + (":retire" if retirement else ":activate")
        if (acknowledgement.retirement_id if retirement else acknowledgement.activation_id) != expected_operation:
            raise ContentError("receipt_operation_mismatch", "Receipt does not bind the original delivery operation")
        if not retirement and acknowledgement.publication_generation != operation["document"]["export"]["publication"]["publication_generation"]:
            raise ContentError("receipt_generation_mismatch", "Receipt confirms a different publication generation")
        if receipt.get("transport") != "authenticated_learning_http" or not receipt.get("endpoint"):
            raise ContentError("receipt_transport_missing", "Operator transport provenance is required")
        with self.service._transaction() as database:
            lock_scope(database, operation["source_id"], identifier)
            row = database.execute("SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if row["state"] not in ("applying", "completed"):
                raise ContentError("application_not_authorized", "Receipt cannot replace durable application authorization")
            if row["state"] == "completed" and row["document"].get("receipt") != receipt:
                raise ContentError("receipt_conflict", "Completed receipt is immutable")
            database.execute("UPDATE content.bridge_operations SET state='completed',document=document || %s,updated_at=now() WHERE id=%s", (Jsonb({"receipt": receipt}), identifier))
        return self.operation(identifier)

    def record_terminal_failure(self, identifier, failure):
        """Persist an independently verified outcome without undoing source history.

        The local CLI obtains `failure` from receipt_transport; this domain port
        owns only the atomic state transition and rechecks its frozen delivery.
        """
        operation = self.operation(identifier)
        if (failure.get("transport") != "authenticated_learning_http"
                or failure.get("state") != "failed"
                or failure.get("content_operation_id") != identifier
                or failure.get("workspace_id") != self.service.workspace_id
                or not isinstance(failure.get("delivery"), dict)
                or failure.get("delivery") != operation["document"].get("delivery")
                or failure.get("learning_operation_id") != identifier + (":retire" if failure["delivery"].get("retirement") else ":activate")
                or failure.get("endpoint") != failure["delivery"].get("endpoint")
                or not isinstance(failure.get("receipt_sha256"), str)
                or len(failure["receipt_sha256"]) != 64):
            raise ContentError("failure_receipt_mismatch", "Failure does not bind this authorized delivery")
        with self.service._transaction() as database:
            # Terminal replay must also work after a successor reserves the source.
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("content:scope:" + operation["source_id"],))
            row = database.execute("SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if row["state"] == "failed":
                if row["document"].get("failure") != failure:
                    raise ContentError("terminal_failure_conflict", "Terminal failure is immutable")
            elif row["state"] == "applying" and row["document"].get("delivery") == failure["delivery"]:
                lock_scope(database, operation["source_id"], identifier)
                if row["document"].get("receipt") is not None:
                    raise ContentError("terminal_failure_conflict", "A completion receipt cannot become failure")
                database.execute("UPDATE content.bridge_operations SET state='failed',document=document || %s,updated_at=now() WHERE id=%s", (Jsonb({"failure": failure}), identifier))
            else:
                raise ContentError("application_not_authorized", "Terminal failure cannot replace another operation outcome")
        return self.operation(identifier)

    def checkpoint(self, identifier, values):
        if not values or not set(values) <= {"delivery", "snapshots"}:
            raise ContentError("invalid_checkpoint", "Only delivery identity and snapshot observations are supported")
        operation = self.operation(identifier)
        packed_snapshots={key:pack_bridge_document(value) for key,value in values.get('snapshots',{}).items()}
        legacy_hashes={key:digest(value) for key,value in operation['document'].get('snapshots',{}).items()}
        values=dict(values)
        with self.service._transaction() as database:
            lock_scope(database, operation["source_id"], identifier)
            row = database.execute("SELECT state,document FROM content.bridge_operations WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if row["state"] not in ("applying", "completed"):
                raise ContentError("application_not_authorized", "Checkpoint requires durable application authorization")
            document = row["document"]
            if 'snapshots' in values:
                previous=document.get('snapshots_packed',{})
                for name,item in packed_snapshots.items():
                    if (name in previous and previous[name]['sha256']!=item['sha256'] or
                            name in document.get('snapshots',{}) and legacy_hashes.get(name)!=item['sha256']):
                        raise ContentError('checkpoint_conflict','Snapshot observation identity is immutable')
                values.pop('snapshots')
                values['snapshots_packed']={**packed_snapshots,**previous}
            for key,value in values.items():
                if key != 'snapshots_packed' and key in document and document[key] != value:
                    raise ContentError("checkpoint_conflict", "Delivery identity is immutable")
            database.execute("UPDATE content.bridge_operations SET document=document || %s,updated_at=now() WHERE id=%s", (Jsonb(values),identifier))
        return self.operation(identifier)

    def export(self, publication_id, generation, manifest=None):
        if manifest is None:
            prepared = self.service._one("SELECT document->'export' AS export FROM content.bridge_operations WHERE document->'export'->'publication'->>'publication_id'=%s AND document->'export'->'publication'->>'publication_generation'=%s", (publication_id,str(generation)))
            if prepared and prepared.get('export'):
                return prepared['export']
            packed=self.service._one("SELECT document->'export_packed' AS packed FROM content.bridge_operations WHERE document->'export_identity'->>'publication_id'=%s AND document->'export_identity'->>'publication_generation'=%s",(publication_id,str(generation)))
            if packed and packed.get('packed'):
                return unpack_bridge_document(packed['packed'])
        row = self.service._one("SELECT * FROM content.publications WHERE publication_id=%s AND generation=%s", (publication_id, generation))
        if not row:
            raise ContentError("publication_not_found", "Immutable publication is unavailable")
        source = SourceSnapshot.model_validate(row["provenance"]["snapshot"])
        manifest = manifest or {"complete": False, "provenance": {"kind": "unavailable"}, "legacy_aliases": {}, "reentry": []}
        definitions = json.loads(row["payload"])
        by_card_id = {value["card_id"]: value for value in definitions["definitions"]}
        definition_by_key = {item.key: by_card_id[stable_id(source.workspace_id, source.source_key, "card", item.key)] for item in source.exercises}
        repertoire_by_key = {item.key: item for item in source.repertoires}
        routes, legal_replies = [], {}
        authorized = {(item["route_key"], item["policy_key"], item["ply"]) for item in manifest["reentry"]}
        for membership in source.memberships:
            authored = next(item for item in source.routes if item.key == membership.route_key)
            repertoire = repertoire_by_key[membership.repertoire_key]
            bindings = [{"ply": binding.ply, "card_id": definition_by_key[binding.exercise_key]["card_id"],
                "reentry": (binding.route_key, binding.policy_key, binding.ply) in authorized}
                for binding in source.bindings if (binding.route_key, binding.policy_key) == (membership.route_key, membership.policy_key)]
            route = {"repertoire_id": stable_id(source.workspace_id, source.source_key, "repertoire", repertoire.key),
                "route_id": stable_id(source.workspace_id, source.source_key, "route", membership.route_key),
                "policy_id": stable_id(source.workspace_id, source.source_key, "policy", membership.policy_key),
                "route_key": membership.route_key, "policy_key": membership.policy_key, "repertoire_key": membership.repertoire_key,
                "repertoire_title": repertoire.title,
                "learner_color": repertoire.learner_color, "starting_fen": authored.starting_fen, "moves": authored.moves, "bindings": bindings}
            if authored.authority_start_ply != 0:
                route["authority_start_ply"] = authored.authority_start_ply
            routes.append(route)
            board = chess.Board(authored.starting_fen)
            for ply in range(len(authored.moves) + 1):
                if board.turn != (repertoire.learner_color == "white"):
                    key = canonical_position(board.fen())
                    if key not in legal_replies:
                        if len(legal_replies)>=32768:
                            raise ContentError('bridge_inventory_limit','Complete bridge export exceeds 32768 opponent positions; no Learning activation was attempted')
                        legal_replies[key] = {"position_key": key, "complete": True, "generation": generation,
                            "inventory_version": 1, "moves": sorted(move.uci() for move in board.legal_moves), "scopes": []}
                if ply < len(authored.moves):
                    board.push_uci(authored.moves[ply])
        document = {"schema_version": 1, "kind": "content_bridge_export", "workspace_id": source.workspace_id,
            "source": {key: row["provenance"][key] for key in ("source_id", "source_key", "source_digest", "source_revision", "inventory_version")},
            "publication": definitions, "publication_digest": row["digest"], "complete": manifest["complete"],
            "provenance": manifest["provenance"], "routes": sorted(routes, key=lambda item: (item["route_id"], item["policy_id"])),
            "legacy_aliases": {stable_id(source.workspace_id, source.source_key, "card", key): value for key, value in manifest["legacy_aliases"].items()},
            "historical_mapping_unknown": [stable_id(source.workspace_id, source.source_key, "card", key) for key in manifest.get("historical_mapping_unknown", [])],
            "legal_replies": sorted(legal_replies.values(), key=lambda item: item["position_key"])}
        # Coverage is conditioned on an established route prefix and policy, never
        # FEN coincidence. Enumerate the complete legal support, including absence.
        budget = {'bytes': len(canonical(document).encode()), 'maximum': 64*1024*1024-83}
        if budget['bytes'] > budget['maximum']:
            raise ContentError('bridge_export_limit', 'Complete bridge export exceeds 64 MiB; no Learning activation was attempted')
        nodes = {}
        for route in routes:
            board = chess.Board(route['starting_fen'])
            for ply in range(len(route['moves'])+1):
                if board.turn != (route['learner_color']=='white'):
                    context = (route['repertoire_id'],route['policy_id'],route['learner_color'],route['starting_fen'],tuple(route['moves'][:ply]))
                    nodes.setdefault(context,[]).append((route,ply,board.fen(en_passant='fen')))
                if ply<len(route['moves']):
                    board.push_uci(route['moves'][ply])
        definitions_by_id = {item['card_id']:item for item in definitions['definitions']}
        for context,occurrences in nodes.items():
            board=chess.Board(occurrences[0][2])
            # Authored continuation without an executable binding is missing
            # identity authority, not evidence that the response is absent.
            complete = manifest['complete'] and all(
                route.get('authority_start_ply',0) is not None and ply >= route.get('authority_start_ply',0) and ply < len(route['moves']) and any(binding['ply'] == ply + 1 for binding in route['bindings'])
                for route, ply, _ in occurrences)
            coverage=[]
            for move in sorted(board.legal_moves,key=lambda value:value.uci()):
                accepted={}
                for route,ply,_ in occurrences:
                    if ply<len(route['moves']) and route['moves'][ply]==move.uci():
                        for binding in route['bindings']:
                            if binding['ply']==ply+1:
                                accepted[binding['card_id']]=definitions_by_id[binding['card_id']]
                decision_ids={item['decision_id'] for item in accepted.values()}
                result=board.copy()
                result.push(move)
                coverage.append({'move_uci':move.uci(),'decision_fen':result.fen(en_passant='fen'),
                    'status':'ambiguous' if len(decision_ids)>1 else 'covered' if accepted else 'uncovered' if complete else 'unknown',
                    'responses':list(accepted.values())})
            scope={'repertoire_id':context[0],'policy_id':context[1],'learner_color':context[2],
                'starting_fen':context[3],'history':list(context[4]),'route_contexts':[{'route_id':route['route_id'],'route_key':route['route_key'],'policy_key':route['policy_key'],'repertoire_key':route['repertoire_key'],'ply':ply} for route,ply,_ in occurrences],
                'complete':complete,
                'source_revision':definitions['source_revision'],'publication_generation':generation,'replies':coverage}
            scope['digest']=digest(scope)
            append_export_scope(legal_replies[canonical_position(board.fen())], scope, budget)
        document["export_digest"] = digest(document)
        if len(canonical(document).encode())>64*1024*1024:
            raise ContentError('bridge_export_limit','Complete bridge export exceeds 64 MiB; no Learning activation was attempted')
        return document

    def proposal_draft(self, proposal):
        proposal = ResponseProposal.model_validate(proposal)
        previous_draft = self.service._one("SELECT id,document FROM content.bridge_drafts WHERE document->'proposal'->>'proposal_id'=%s AND document->'proposal'->>'workspace_id'=%s AND source_key=%s", (proposal.proposal_id,proposal.workspace_id,proposal.source_key))
        if previous_draft:
            if previous_draft['document']['proposal']!=proposal.model_dump(mode='json'):
                raise ContentError('proposal_identity_conflict','Proposal idempotency identity was reused with different contents')
            return self.draft_show(previous_draft['id'])
        current = self.service.source_show(proposal.source_key)
        source = SourceSnapshot.model_validate(current["snapshot"])
        source_digest = hashlib.sha256(canonical_source(source).encode()).hexdigest()
        if proposal.workspace_id != source.workspace_id or current["requested_revision"] != proposal.expected_source_revision or source_digest != proposal.source_digest:
            raise ContentError("stale_proposal", "Proposal does not bind the current workspace/source revision/digest")
        repertoire = next((item for item in source.repertoires if item.key == proposal.repertoire_key), None)
        if repertoire is None or repertoire.learner_color != proposal.learner_color or not any(item.key == proposal.policy_key and item.repertoire_key == repertoire.key for item in source.policies):
            raise ContentError("proposal_policy_mismatch", "Proposal policy/color does not match source")
        route = next((item for item in source.routes if item.key == proposal.route_key), None)
        if route is None or proposal.anchor_ply >= len(route.moves) or not any(item.route_key == route.key and item.policy_key == proposal.policy_key for item in source.memberships):
            raise ContentError("proposal_anchor_missing", "Proposal requires an established route context")
        board = chess.Board(route.starting_fen)
        for move in route.moves[:proposal.anchor_ply]:
            board.push_uci(move)
        if board.turn == (proposal.learner_color == "white"):
            raise ContentError("proposal_anchor_color", "Insertion anchor must be the opponent decision")
        board.push_uci(proposal.opponent_move)
        if board.fen(en_passant="fen") != proposal.decision_fen:
            raise ContentError("proposal_anchor_fen", "Opponent deviation does not reach the exact proposed position")
        document = source.model_dump(mode="json")
        key = "proposal-" + digest([proposal.policy_key, proposal.decision_fen])
        if any(item["key"] == key for item in document["exercises"]):
            raise ContentError("proposal_decision_exists", "Decision already exists; reconcile an explicit source edit")
        document["exercises"].append({"key": key, "decision_key": key, "policy_key": proposal.policy_key,
            "starting_fen": proposal.decision_fen, "prompt": "Play your approved response",
            "specification": {"type": "move_line", "mode": "single" if all(len(line)==1 for line in proposal.accepted_lines) else "stepwise_line", "grading_policy": "reference", "accepted_lines": proposal.accepted_lines}})
        for index, line in enumerate(proposal.accepted_lines):
            route_key = key + "-" + str(index)
            document["routes"].append({"key": route_key, "starting_fen": route.starting_fen,
                "moves": route.moves[:proposal.anchor_ply] + [proposal.opponent_move] + line, "authority_start_ply": route.authority_start_ply})
            document["memberships"].append({"route_key": route_key, "policy_key": proposal.policy_key, "repertoire_key": proposal.repertoire_key})
            document["bindings"].extend({**item, "route_key": route_key} for item in source.model_dump(mode="json")["bindings"]
                if item["route_key"] == route.key and item["policy_key"] == proposal.policy_key and item["ply"] < proposal.anchor_ply)
            document["bindings"].append({"route_key": route_key, "policy_key": proposal.policy_key, "ply": proposal.anchor_ply + 1, "exercise_key": key})
        # Retain original provenance/alias/re-entry instead of relabeling imported scope.
        previous = self.service._one("SELECT d.document FROM content.bridge_operations o JOIN content.bridge_drafts d ON d.id=o.draft_id WHERE o.source_id=%s AND o.state='completed' ORDER BY o.updated_at DESC LIMIT 1", (current["id"],))
        if previous is None:
            raise ContentError("proposal_provenance_unavailable", "Proposal application needs the original complete import manifest")
        return self.draft(document, previous["document"]["manifest"], proposal.expected_source_revision, proposal.model_dump(mode="json"))
