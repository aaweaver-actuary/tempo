"""Merge accepted source and engine facts without database access.

The store owns evidence capture; this module owns finding enrichment, proposal
assembly, uncertain engine identities and the existing ranking order.
"""
from ..identity import digest
from .classify import base_finding, rank_key, engine_association
from .engine import comparison
from .sources import source_advice


def merge_findings(workspace,run,rows,results,source,games,findings,unknown,source_records):
    for advice in source_advice(source["payload"],source_records):
        existing=next((f for f in findings if f["finding_id"]==advice["finding_id"]),None)
        if existing:
            existing["source_evidence"]+=advice["source_evidence"]
            existing["evidence_ids"]=sorted(set(existing["evidence_ids"]+advice["evidence_ids"]))
        else:
            findings.append(advice)
    incompatible_positions=set()
    for row in rows:
        if not row["accepted_root"]:
            continue
        root=results[row["accepted_root"]]
        played=results.get(row["accepted_played"])
        try:
            evidence=comparison(root,played,row["payload"]["actual_move"])
        except ValueError as error:
            if str(error)!="incompatible_engine_profile":
                raise
            incompatible_positions.add(row["position_id"])
            unknown.append({"game_id":row["payload"]["game_id"],"ply":row["payload"]["ply"],"reason":"incompatible_engine_profile"})
            continue
        for existing in findings:
            representative=existing["representative"]
            if (representative["game_id"],representative["ply"])==(row["payload"]["game_id"],row["payload"]["ply"]):
                existing["engine"]=evidence
                anchor=existing.get("proposal_anchor")
                line=root["variations"][0]["moves"][:5]
                if len(line)%2==0:line=line[:-1]
                if anchor and source["payload"]["producer"]=="a-analysis-export" and evidence["status"] not in {"played_analysis_pending", "insufficient_quality"} and root["variations"][0]["depth"]>=18 and line:
                    existing["response_proposal"]={"schema_version":1,"kind":"response_proposal","status":"draft","proposal_id":"c:"+existing["finding_id"][-64:]+":"+str(run["requested_generation"]),"workspace_id":workspace,**anchor,"learner_color":existing["learner_color"],"expected_source_revision":existing["lineage"]["source_revision"],"source_digest":existing["lineage"]["source_digest"],"opponent_move":existing["opponent_move"],"decision_fen":representative["fen"],"accepted_lines":[line],"provenance":{"kind":"analysis_proposal","evidence_refs":existing["evidence_ids"]+[row["accepted_root"]]}}
                    existing["actions"]["response_proposal"]={"available":True}
                    existing["proposal_cost"]=sum(1 for index in range(0,len(line),2))
        if not evidence["significant"]:
            continue
        game = next(game for game in games if game["game_id"] == row["payload"]["game_id"])
        selection, associations = engine_association(source["payload"], game, row["payload"])
        if selection is None:
            selection = {"repertoire_id": None, "policy_id": None, "lineage": None}
        item=base_finding("engine_opportunity",{**selection,"workspace_id":workspace},row["payload"],row["payload"]["actual_move"],"stronger_continuation_supported_by_search")
        # Engine incidents are game/history specific; do not aggregate incompatible roots.
        item["finding_id"]=digest({"semantic":item["finding_id"],"game":row["payload"]["game_id"],"history":row["payload"]["history"],"starting_fen":row["payload"]["starting_fen"],"ply":row["payload"]["ply"]})
        item.update(engine=evidence,evidence_ids=[row["accepted_root"]]+([row["accepted_played"]] if row["accepted_played"] else []),eligible_encounter_games=None,actual_frequency=None)
        item["source_evidence"] = [{"kind": "repertoire_association", **association} for association in associations]
        item["evidence_ids"] = sorted(set(item["evidence_ids"] + [identity for association in associations for identity in association["evidence_ids"]]))
        if selection["lineage"] is None:
            item["uncertainty"].append("ambiguous_repertoire_association" if associations else "repertoire_association_unknown")
        findings.append(item)
    findings.sort(key=lambda finding:rank_key(finding,run["payload"]["ranking_source"],run["payload"].get("ranking_evidence_id")))
    return findings,unknown,incompatible_positions
