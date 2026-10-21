"""Existing Analysis read, cursor and export workflows through fixed owner reads."""
import base64
import json

from ..errors import DomainError
from ..identity import canonical_json
from ..serialization import public


def require_workspace(workspace,current):
    if workspace != current():
        raise DomainError("workspace_mismatch",403)


def read_inputs(workspace,transaction):
    with transaction() as operations:
        rows = operations.inputs()
    return public({"schema_version":1,"workspace_id":workspace(),"items":rows})


def read_input(input_id,revision,transaction):
    with transaction() as operations:
        row = operations.input(input_id,revision)
        pages = operations.pages(input_id,revision)
    return row,[g for p in pages for g in p["payload"]["games"]]


def run_status(run,workspace,operations):
    counts=operations.counts(run)
    return public({"schema_version":1,"workspace_id":workspace(),"run_id":run["run_id"],"revision":run["revision"],"state":run["state"],"scope":{key:value for key,value in run["payload"].items() if key!="derivation_version"},"derivation_version":run["payload"].get("derivation_version",1),"recovery_required":"legacy_analysis_revision_required" if run["payload"].get("derivation_version",1)!=2 else None,"progress":counts,"requested_generation":run["requested_generation"],"published_generation":run["published_generation"],"last_complete_generation":run["last_complete_generation"],"engine_scan_complete":sum(v for k,v in counts.items() if k!="complete")==0})


def read_runs(workspace,transaction):
    with transaction() as operations:
        rows=operations.runs()
        return {"schema_version":1,"workspace_id":workspace(),"items":[operations.status(r) for r in rows]}


def read_receipt(command_id,transaction):
    with transaction() as operations:
        row=operations.receipt(command_id)
        return {**row["payload"], "request_digest": row["digest"]}


def read_report(run_id,workspace,transaction,*,after=None,limit=50,category=None,dismissal=None,generation=None):
    if not 1<=limit<=100 or category not in (None,"repertoire_miss","practical_gap","engine_opportunity") or dismissal not in (None,"open","dismissed"):
        raise DomainError("invalid_analysis_filter",422)
    with transaction() as operations:
        run=operations.head(run_id)
        selected=generation or run["published_generation"]
        report_revision=run["revision"]
        if selected is None:
            previous=operations.previous_report(run_id,run)
            if previous:
                selected=previous["published_generation"];report_revision=previous["revision"]
        elif generation is not None:
            historical=operations.report_revision(run_id,selected)
            if historical:report_revision=historical["revision"]
        if selected is None:
            return {"schema_version":1,"workspace_id":workspace(),"run_id":run_id,"status":"pending","items":[],"next_cursor":None,"generation":None,"derivation_version":run["payload"].get("derivation_version",1),"reasons":["legacy_analysis_revision_required"] if run["payload"].get("derivation_version",1)!=2 else []}
        report=operations.report(run_id,report_revision,selected)
        cursor_scope={"version":1,"workspace":workspace(),"run":run_id,"revision":report_revision,"head_revision":run["revision"],"generation":selected,"category":category,"dismissal":dismissal,"ordering":report["payload"].get("ordering","category-source-rank-semantic-v1")}
        last=-1
        if after:
            try:
                if len(after)>4096:raise ValueError()
                cursor=json.loads(base64.urlsafe_b64decode(after.encode()))
                last=cursor.pop("last")
                if type(last) is not int or last<0:
                    raise ValueError()
            except Exception:
                raise DomainError("invalid_analysis_cursor",422) from None
            if cursor!=cursor_scope:
                raise DomainError("stale_analysis_cursor")
        rows=operations.report_items(run_id,report_revision,selected,last,category,dismissal,limit)
    next_cursor=base64.urlsafe_b64encode(canonical_json({**cursor_scope,"last":rows[limit-1]["ordinal"]}).encode()).decode() if len(rows)>limit else None
    from tempo_intelligence.analysis.read_models import ReportPage
    return ReportPage.model_validate({**report["payload"],"status":"current" if report_revision==run["revision"] and selected==run["requested_generation"] else "stale","items":[r["payload"] for r in rows[:limit]],"next_cursor":next_cursor}).model_dump(mode="json")


def read_finding(run_id,finding_id,generation,workspace,transaction):
    with transaction() as operations:
        run=operations.head(run_id)
        visible=run["published_generation"]
        if visible is None:
            previous=operations.previous_generation(run_id,run)
            visible=previous["published_generation"] if previous else None
        row=operations.finding(run_id,generation,finding_id,visible)
    return {"schema_version":1,"workspace_id":workspace(),"run_id":run_id,"generation":generation,"finding":row["payload"]}


def export_finding(run_id,finding_id,generation,kind,finding,report,workspace):
    item=finding()(run_id,finding_id,generation)["finding"]
    if kind=="response-proposal" and report()(run_id,generation=generation)["derivation_version"]!=2:
        raise DomainError("legacy_analysis_revision_required")
    if kind=="response-proposal" and item["actions"]["response_proposal"]["available"]:
        return item["response_proposal"]
    if kind!="context":
        raise DomainError("validated_A_proposal_unavailable",422)
    context={key:value for key,value in item.items() if key not in {"engine","response_proposal","actions"}}
    return {"schema_version":1,"workspace_id":workspace(),"kind":"informational_gap_context" if item["category"]=="practical_gap" else "informational_finding_context","status":"proposal_only","finding":context,"authority":"No approval, publication, activation or Learning effect"}


def read_remaining(run_id,workspace,transaction,after=None,limit=64):
    if not 1<=limit<=100:
        raise DomainError("invalid_analysis_limit",422)
    with transaction() as operations:
        run=operations.head(run_id)
        scope={"workspace":workspace(),"run":run_id,"revision":run["revision"],"ordering":"remaining-ordinal-v1"}
        last=-1
        if after:
            try:
                if len(after)>4096:raise ValueError()
                cursor=json.loads(base64.urlsafe_b64decode(after.encode()));last=cursor.pop("last")
                if type(last) is not int or last<0:raise ValueError()
            except Exception:raise DomainError("invalid_remaining_cursor",422) from None
            if cursor!=scope:raise DomainError("stale_remaining_cursor")
        rows=operations.remaining(run_id,run,last,limit)
    cursor=base64.urlsafe_b64encode(canonical_json({**scope,"last":rows[limit-1]["ordinal"]}).encode()).decode() if len(rows)>limit else None
    return {"schema_version":1,"workspace_id":workspace(),"run_id":run_id,"revision":run["revision"],"items":rows[:limit],"next_after":cursor}


def read_engine_result(result_id,workspace,transaction):
    with transaction() as operations:
        row=operations.engine_result(result_id)
    return {"schema_version":1,"workspace_id":workspace(),"result_id":result_id,**row}


def read_input_status(input_id,revision,workspace,transaction):
    with transaction() as operations:
        row=operations.input(input_id,revision)
        pages=operations.page_status(input_id,revision)
    return {"schema_version":1,"workspace_id":workspace(),"manifest":row["payload"],"digest":row["digest"],"state":row["state"],"accepted_pages":pages}
