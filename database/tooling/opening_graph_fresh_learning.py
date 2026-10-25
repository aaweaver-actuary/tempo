"""Pure fresh-opening plans around the existing public Content/bridge workflow.

No acquisition, SQL, transport, service import, migration or live reset occurs.
Composition executes the installed public CLI and independently observes its
explicit disposable target. Historical transfer is deliberately not a gate.
"""

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class FreshLearningError(ValueError):
    """The explicit disposable target or public operation is not ready."""


def _require(condition, message):
    if not condition:
        raise FreshLearningError(message)


def _text(value):
    return type(value) is str and bool(value.strip())


def _integer(value):
    return type(value) is int and value > 0


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _sha256(value):
    return type(value) is str and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _json_copy(value):
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
        _require(copied == value, "exact JSON input required")
        return copied
    except (TypeError, ValueError, RecursionError):
        raise FreshLearningError("finite JSON input required") from None


def _capability(capability, *arguments):
    try:
        return capability(*(deepcopy(value) for value in arguments))
    except Exception:
        raise FreshLearningError("public injected capability unavailable or rejected input") from None


def validate_fresh_target(target):
    """Validate the observer's packet; this function does not observe a target.

    `empty_learning` certifies migrated workspace/settings metadata only: no
    existing content, user activity, old schedules, receipts, queues or jobs.
    The observer owns actual read-only storage checks and target selection.
    """
    target = _json_copy(target)
    _require(type(target) is dict and set(target) == {
        "target_id", "workspace_id", "timezone", "disposable", "initialized",
        "empty_learning", "observed_at"}, "exact fresh-target observation required")
    _require(all(_text(target[key]) for key in
                 ("target_id", "workspace_id", "timezone", "observed_at")),
             "explicit target identity, workspace, timezone and observation time required")
    _require(all(target[key] is True for key in ("disposable", "initialized", "empty_learning")),
             "initialized empty disposable Learning target required")
    try:
        ZoneInfo(target["timezone"])
        observed = datetime.fromisoformat(target["observed_at"].replace("Z", "+00:00"))
        _require(observed.utcoffset() is not None, "timezone-qualified observation time required")
    except (ValueError, ZoneInfoNotFoundError):
        raise FreshLearningError("explicit effective timezone and observation time required") from None
    return target


def prepare_fresh_learning(export, *, target, operation_id, content_cli,
                           learning_origin, operator_token_file, initial_status,
                           verify_export):
    """Plan the complete EXISTING `tempo-a-bridge ... apply operation_id` CLI.

    Content's public authored-import workflow supplies the approved operation ID
    and public export. `verify_export` certifies full graph/repertoire/accepted
    definition closure and independent retention of the immutable source bundle.
    The installed bridge owns V2 serialization and durable delivery, not this
    helper. No private sibling import or new emit-bundle API is required.
    """
    target = validate_fresh_target(target)
    export, initial_status = map(_json_copy, (export, initial_status))
    _require(all(_text(value) for value in (operation_id, content_cli, learning_origin, operator_token_file)),
             "explicit public CLI paths, origin and approved operation identity required")
    # The existing public bridge protocol derives this ID and bounds it to 128.
    activation_id = operation_id + ":activate"
    _require(len(activation_id) <= 128, "bounded approved operation identity required")
    parsed = urlsplit(learning_origin)
    _require(parsed.scheme in ("http", "https") and bool(parsed.netloc) and
             not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and
             parsed.path in ("", "/"), "explicit credential-free Learning origin required")
    _require(type(export) is dict and type(export.get("schema_version")) is int and
             export.get("schema_version") == 1 and export.get("kind") == "content_bridge_export" and
             export.get("complete") is True, "complete public Content bridge export v1 required")
    _require(export.get("workspace_id") == target["workspace_id"], "export workspace mismatch")
    _require(_capability(verify_export, export) is True, "public graph/source closure verification required")
    publication, source = export.get("publication"), export.get("source")
    _require(type(publication) is dict and type(source) is dict and
             publication.get("workspace_id") == target["workspace_id"] and
             _text(publication.get("publication_id")) and _integer(publication.get("publication_generation")) and
             _integer(publication.get("source_revision")) and _sha256(source.get("source_digest")),
             "explicit matching public publication/source identity required")
    aliases = export.get("legacy_aliases", {})
    _require(type(aliases) is dict and all(key == value for key, value in aliases.items()),
             "empty fresh target cannot adopt old physical learning objects")
    try:
        _require(type(initial_status) is dict and type(initial_status.get("schema_version")) is int and
                 initial_status.get("schema_version") == 1 and initial_status.get("workspace_id") == target["workspace_id"] and
                 initial_status.get("publication_id") == publication["publication_id"] and
                 initial_status.get("status") == "unavailable" and initial_status.get("accepted") is None and
                 initial_status.get("publication_generation") is None and
                 type(initial_status.get("activation_revision")) is int and initial_status["activation_revision"] == 0,
                 "public status must confirm this fresh publication is unavailable at revision zero")
        due_date = initial_status["local_date"]
        _require(type(due_date) is str and date.fromisoformat(due_date).isoformat() == due_date,
                 "explicit Learning local date required")
    except (KeyError, TypeError, ValueError):
        raise FreshLearningError("exact fresh public status and Learning local date required") from None
    request = {"publication_id": publication["publication_id"], "publication_generation": publication["publication_generation"],
               "expected_activation_revision": 0, "activation_id": activation_id}
    return {
        "target": target, "export": export, "operation_id": operation_id,
        "initial_due_date": due_date, "learning_origin": learning_origin.rstrip("/"),
        "apply_argv": ["tempo-a-bridge", "--content-cli", content_cli, "--learning-origin", learning_origin,
                       "--operator-token-file", operator_token_file, "apply", operation_id],
        "activation_request": request,
        "receipt_path": "/internal/publications/operations/" + quote(activation_id, safe=""),
        "status_path": "/internal/publications/" + quote(publication["publication_id"], safe="") +
                       "/status?generation=" + str(publication["publication_generation"]),
    }


def verify_fresh_learning(plan, *, operator_result, receipt, status, verify_fresh_effects):
    """Check public CLI completion, exact Learning receipt and active identity.

    The injected `verify_fresh_effects(plan, status)` independently reads actual
    complete graph/default-card state, no old activity, and only this activation's
    new receipts and required refresh job. Echoing expected source is not proof.
    """
    plan, operator_result, receipt, status = map(_json_copy, (plan, operator_result, receipt, status))
    try:
        target = validate_fresh_target(plan["target"])
        export, request = plan["export"], plan["activation_request"]
        publication = export["publication"]
        identity, generation = request["publication_id"], request["publication_generation"]
        _require(operator_result.get("id") == plan["operation_id"] and operator_result.get("state") == "completed",
                 "public Content operation has not completed")
        expected = {"contract_version": 1, "workspace_id": target["workspace_id"],
                    "activation_id": request["activation_id"], "publication_id": identity,
                    "publication_generation": generation, "activation_revision": 1, "status": "activated", "reason": None}
        _require(receipt.get("operation_id") == request["activation_id"] and receipt.get("workspace_id") == target["workspace_id"] and
                 receipt.get("command_name") == "publication.activate.v2" and receipt.get("state") == "complete" and
                 _canonical(receipt.get("request")) == _canonical(request) and
                 _canonical(receipt.get("response")) == _canonical(expected) and not receipt.get("error"),
                 "exact completed activation operation receipt required")
        _require(type(status.get("schema_version")) is int and status.get("schema_version") == 1 and
                 status.get("workspace_id") == target["workspace_id"] and status.get("publication_id") == identity and
                 type(status.get("publication_generation")) is int and status["publication_generation"] == generation and
                 type(status.get("activation_revision")) is int and status["activation_revision"] == 1 and status.get("status") == "active",
                 "fresh publication is not active at its exact first revision")
        accepted = status.get("accepted", {})
        required = {"workspace_id": target["workspace_id"], "publication_id": identity,
                    "publication_generation": generation, "source_revision": publication["source_revision"],
                    "source_digest": export["source"]["source_digest"], "publication_digest": _digest(publication),
                    "bundle_schema_version": 2}
        _require(all(_canonical(accepted.get(key)) == _canonical(value) for key, value in required.items()),
                 "accepted source/definition identity mismatch")
        _require(_sha256(accepted.get("routes_digest")) and _sha256(status.get("bundle_digest")) and
                 status["bundle_digest"] == accepted.get("bundle_digest"), "accepted/serving graph bundle digest mismatch")
        _require(_capability(verify_fresh_effects, plan, status) is True,
                 "independent fresh-target graph/defaults verification required")
        return expected
    except (KeyError, TypeError, AttributeError):
        raise FreshLearningError("complete public plan, operation, receipt and serving status required") from None
