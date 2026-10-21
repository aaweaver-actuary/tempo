"""Existing report derivation, dismissal projection and publication workflow."""
from collections import defaultdict

from ..errors import DomainError
from ..identity import digest


def capture_run(run_id,lookup,transaction):
    with transaction() as operations:
        run,rows,results,dismissals=operations.snapshot(run_id)
    source,games=lookup()(run["payload"]["input_id"],run["payload"]["input_revision"])
    return run,rows,results,dismissals,source,games


def derive_report(run_id,before_publish,capture,evidence,workspace,read_transaction,transaction):
    run,rows,results,dismissals,source,games=capture()(run_id)
    if run["published_generation"]==run["requested_generation"]:
        return False
    legacy_metadata=None
    if run["payload"].get("derivation_version",1)!=2:
        with read_transaction() as operations:
            saved=operations.legacy_report(run_id,run)
            if not saved:
                raise DomainError("legacy_analysis_revision_required")
            legacy_metadata=saved["payload"]
            findings=operations.legacy_findings(run_id,run)
        unknown=legacy_metadata.get("unknown_coverage",[])
        incompatible_positions=set()
    else:
        findings,unknown,incompatible_positions=evidence()(run,rows,results,source,games)
    automatic_reopens=[]
    incident_findings=defaultdict(list)
    for item in findings:incident_findings[item["incident_id"]].append({"finding_id":item["finding_id"],"category":item["category"]})
    for item in findings:
        item["related_findings"]=[other for other in incident_findings[item["incident_id"]] if other["finding_id"]!=item["finding_id"]]
        item["dismissal_state"]="open"
        dismissed=dismissals.get(item["finding_id"])
        if legacy_metadata is None:
            item["evidence_revision"]=digest(item)
        item["finding_revision"]=run["requested_generation"]
        if dismissed and dismissed["state"]=="dismissed" and len(set(item["supporting_games"])-set(dismissed["supporting_games"]))<3:
            item["dismissal_state"]="dismissed"
        if dismissed and dismissed["state"]=="dismissed" and item["dismissal_state"]=="open":
            automatic_reopens.append((item["finding_id"],dismissed["revision"],item["supporting_games"]))
        item["actions"]["dismiss"]={"available":item["dismissal_state"]!="dismissed","reason":"already_dismissed" if item["dismissal_state"]=="dismissed" else None}
        item["actions"]["reopen"]={"available":item["dismissal_state"]=="dismissed","reason":"not_dismissed" if item["dismissal_state"]!="dismissed" else None}
        item["input_id"]=source["input_id"];item["input_revision"]=source["revision"]
    from tempo_intelligence.analysis.read_models import Finding
    findings=[Finding.model_validate(item).model_dump(mode="json") for item in findings]
    generation=run["requested_generation"]
    for start in range(0,len(findings),64):
        with transaction() as operations:
            for ordinal,item in enumerate(findings[start:start+64],start):
                operations.stage_finding(run_id,run,generation,item,ordinal)
    complete=not incompatible_positions and source["payload"]["completeness"]=="complete" and all(r["state"]=="complete" and r["error_code"] is None for r in rows)
    metadata={"derivation_version":2,"ordering":"source-rank-semantic-v2","schema_version":1,"workspace_id":workspace(),"run_id":run_id,"revision":run["revision"],"generation":generation,"input_id":source["input_id"],"input_revision":source["revision"],"input_producer":source["payload"]["producer"],"source_provenance":(source["payload"].get("producer_manifest") or {}).get("provenance",{"kind":"synthetic"}),"scope":{key:source["payload"][key] for key in ("game_ids","window_start","window_end","selections","producer_version","source_artifact_digest")},"source_completeness":source["payload"]["completeness"],"reasons":source["payload"]["reasons"]+(["incompatible_engine_profile"] if incompatible_positions or any(row["error_code"]=="incompatible_engine_profile" for row in rows) else []),"unknown_coverage":unknown[:100],"unknown_coverage_count":len(unknown),"engine_scan_complete":not incompatible_positions and all(r["state"]=="complete" for r in rows),"unprocessed_count":sum(r["state"]!="complete" or r["position_id"] in incompatible_positions for r in rows),"engine_availability":"unavailable" if any(r["error_code"]=="engine_unavailable" for r in rows) else "pending_or_available","insufficient_quality_count":sum(r["error_code"]=="insufficient_quality" for r in rows),"finding_count":len(findings),"complete":complete,"policy_version":"c-analysis-v1"}
    if legacy_metadata is not None:
        complete=legacy_metadata["complete"]
        metadata={**legacy_metadata,"generation":generation,"derivation_version":1,"ordering":legacy_metadata.get("ordering","category-source-rank-semantic-v1")}
    if before_publish:
        before_publish()
    with transaction() as operations:
        if not operations.admit_publication(run_id,run,generation,findings,dismissals):
            return False
        operations.reopen_findings(run_id,automatic_reopens)
        operations.save_report(run_id,run,generation,metadata)
        pending=any(r["state"] in {"pending","running","retry_wait"} for r in rows)
        state="complete" if complete else "pending" if pending else "budget_exhausted" if any(r["state"]=="unbudgeted" for r in rows) else "partial"
        operations.publish_generation(run_id,run,generation,complete,state)
    return True


def derive_next_report(derive,transaction):
    with transaction() as operations:
        row=operations.next_run()
    return derive()(row["run_id"]) if row else False


