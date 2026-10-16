"""Pure conditional evidence mathematics. Future #156 seam; no scheduling effects."""
from collections import defaultdict, deque
import chess
from ..identity import digest
from .models import POLICY


def positions(game):
    board = chess.Board(game["starting_fen"])
    for ply, move in enumerate(game["moves"]):
        if board.turn == (game["learner_color"] == "white"):
            yield {"game_id": game["game_id"], "learner_id":game.get("learner_id"),"provider":game.get("provider"),"provider_game_id":game.get("provider_game_id"), "source_revision": game["source_revision"],
                   "source_digest": game["source_digest"], "ply": ply, "fen": board.fen(en_passant="fen"),
                   "actual_move": move, "learner_color": game["learner_color"],
                   "played_at":game.get("played_at"),"starting_fen": game["starting_fen"], "history": game["moves"][:ply]}
        board.push_uci(move)


def ordered_positions(games):
    queues = []
    for game in sorted(games, key=lambda g: g["game_id"]):
        items = list(positions(game))
        flagged = {c["ply"] for c in game["contexts"] if c["status"] == "missed" or c["coverage"] == "uncovered"}
        middle = items[len(items)//2]["ply"] if items else -1
        items.sort(key=lambda p: (0 if p["ply"] in flagged else 1 if p["ply"] == middle else 2, p["ply"]))
        queues.append(deque(items))
    result = []
    while any(queues):
        for queue in queues:
            if queue:
                result.append(queue.popleft())
    return result


def base_finding(kind, selection, position, target, reason):
    identity = {"workspace": selection["workspace_id"], "kind": kind,
                "repertoire": selection["repertoire_id"], "policy": selection["policy_id"],
                "color": position["learner_color"], "position": " ".join(chess.Board(position["fen"]).fen(en_passant="legal").split()[:4]), "target": target}
    return {"finding_id": digest(identity), "category": kind, "policy_version": POLICY,
            "workspace_id": selection["workspace_id"], "repertoire_id": selection["repertoire_id"],
            "policy_id": selection["policy_id"], "learner_color": position["learner_color"],
            "lineage": selection["lineage"], "representative": position,
            "incident_id": digest({"workspace":selection["workspace_id"],"game":position["game_id"],"ply":position["ply"],"policy":selection["policy_id"],"position":position["fen"]}),
            "supporting_games": [position["game_id"]], "supporting_game_count": 1,
            "evidence_ids": [], "evidence_status": "current", "reason_codes": [reason],
            "uncertainty": [], "expected_moves": [], "card_id": None, "decision_id": None,
            "engine": None, "gap_kind": None, "source_evidence": [],
            "delivery_status": "not_required", "learning_settlement": "unknown",
            "activation_status": selection["lineage"]["activation_status"], "proposal_status": "not_created",
            "dismissal_state": "open", "proposal_cost": None,
            "actions": {"dismiss": {"available": True}, "reopen": {"available": False, "reason": "not_dismissed"},
                        "context_export": {"available": True}, "response_proposal": {"available": False, "reason": "validated_A_proposal_unavailable"}}}


def source_findings(manifest, games):
    selections = {(s["repertoire_id"], s["policy_id"], s["learner_color"]): {**s,"workspace_id":manifest["workspace_id"]} for s in manifest["selections"]}
    incidents, unknown = [], []
    denominators = defaultdict(set)
    opponent_denominators=defaultdict(set)
    incomplete_encounters=set()
    encounter_inventory_complete=manifest["completeness"]=="complete" and all(game.get("opponent_encounters_complete",False) for game in games)
    for game in sorted(games,key=lambda item:item["game_id"]):
        by_ply = {p["ply"]:p for p in positions(game)}
        for encounter in game.get('opponent_encounters',[]):
            key=(encounter['repertoire_id'],encounter['policy_id'],game['learner_color'])
            position_key=' '.join(chess.Board(encounter['fen']).fen(en_passant='legal').split()[:4])
            if encounter['scope_complete']:opponent_denominators[(key,position_key)].add(game['game_id'])
            else:incomplete_encounters.add((key,position_key))
        if not game["contexts"]:
            unknown.append({"game_id":game["game_id"],"ply":None,"reason":"comparison_unavailable"})
        diverged = set()
        for context in sorted(game["contexts"], key=lambda c:(c["ply"],c["policy_id"],c["evidence_id"])):
            key = (context["repertoire_id"],context["policy_id"],game["learner_color"])
            selection = selections.get(key)
            if selection is None:
                continue
            position = by_ply[context["ply"]]
            activated = selection["lineage"]["activation_status"] == "activated"
            if context["reentry_authorized"]:
                diverged.discard(key)
            if context["route_established"] and key not in diverged:
                denominators[(key, " ".join(position["fen"].split()[:4]))].add(game["game_id"])
            if not activated:
                unknown.append({"game_id":game["game_id"],"ply":context["ply"],"reason":"activation_unknown"})
                continue
            # Accepted alternatives take precedence even if a malformed producer says missed.
            if context["status"] == "missed" and position["actual_move"] not in context["expected_moves"] and context["expected_moves"] and context["card_id"] and context["decision_id"]:
                item = base_finding("repertoire_miss",selection,position,context["decision_id"],"accepted_response_not_played")
                item.update(expected_moves=sorted(set(context["expected_moves"])),card_id=context["card_id"],decision_id=context["decision_id"],delivery_status=context["delivery_status"],delivery=context.get("delivery"),evidence_ids=[context["evidence_id"]])
                incidents.append(item)
            supported_gap = (context["coverage"] == "uncovered" and context["route_established"] and context["scope_complete"] and context["scope_revision"] is not None and context["opponent_ply"] is not None and bool(context["route_ids"]) and not context["expected_moves"])
            if supported_gap and key not in diverged and (not context["after_divergence"] or context["reentry_authorized"]):
                item = base_finding("practical_gap",selection,position,context["opponent_move"],"observed_uncovered_response")
                opponent_board=chess.Board(position['starting_fen'])
                for move in position['history'][:-1]:opponent_board.push_uci(move)
                item.update(opponent_fen=opponent_board.fen(en_passant='fen'),gap_kind="observed",opponent_move=context["opponent_move"],opponent_ply=context["opponent_ply"],route_ids=context["route_ids"],scope_revision=context["scope_revision"],proposal_anchor=context["proposal_anchor"],evidence_ids=[context["evidence_id"]])
                incidents.append(item)
                diverged.add(key)
            elif context["coverage"] == "unknown" or context["coverage"] == "uncovered" and not supported_gap:
                unknown.append({"game_id":game["game_id"],"ply":context["ply"],"reason":context["reason"]})
    grouped = {}
    for item in incidents:
        identity = item["finding_id"]
        if identity not in grouped:
            grouped[identity] = item
        else:
            current = grouped[identity]
            current["supporting_games"] = sorted(set(current["supporting_games"]+item["supporting_games"]))
            current["evidence_ids"] = sorted(set(current["evidence_ids"]+item["evidence_ids"]))
    for item in grouped.values():
        key = (item["repertoire_id"],item["policy_id"],item["learner_color"])
        denominator = len(denominators[(key," ".join(item["representative"]["fen"].split()[:4]))])
        item["supporting_game_count"] = len(item["supporting_games"])
        item["eligible_encounter_games"] = max(denominator,item["supporting_game_count"]) if item["category"]!="practical_gap" else None
        item["actual_frequency"] = item["supporting_game_count"]/item["eligible_encounter_games"] if item["eligible_encounter_games"] else None
        if item["category"]=="practical_gap":
            pre_key=' '.join(chess.Board(item['opponent_fen']).fen(en_passant='legal').split()[:4])
            eligible=opponent_denominators[(key,pre_key)]
            if encounter_inventory_complete and (key,pre_key) not in incomplete_encounters and set(item['supporting_games'])<=eligible:
                item['eligible_encounter_games']=len(eligible)
                item['actual_frequency']=item['supporting_game_count']/len(eligible)
            else:item["uncertainty"].append("eligible_encounter_denominator_unavailable")
    return list(grouped.values()), unknown


def explorer_coverage(evidence, covered):
    total = evidence["total_games"]
    if not total:
        return {"lower":0.0,"upper":1.0,"status":"unknown"}
    allocated = sum(m["games"] for m in evidence["moves"] if m["uci"] in set(covered))
    missing = evidence["unallocated_games"]
    return {"lower":allocated/total,"upper":min(total,allocated+missing)/total,"status":"bounded" if missing else "exact"}


def maia_distribution(job, result, legal_moves):
    requested = set(job["legal_moves"])
    returned = {m["move"] for m in result["predictions"]}
    if requested != set(legal_moves) or returned != requested:
        raise ValueError("whole_position_legal_set_required")
    probabilities = {m["move"]:m["probability"] for m in result["predictions"]}
    if abs(sum(probabilities.values())-1)>1e-8:
        raise ValueError("invalid_probability_mass")
    return probabilities


def compare_scores(best, played):
    if best["depth"] != played["depth"] or best["depth"] < 18:
        return {"status":"insufficient_quality","significant":False}
    a,b = best["score"],played["score"]
    if a["kind"] == b["kind"] == "centipawns":
        loss = a["value"]-b["value"]
        return {"status":"supported" if loss>=100 else "near_tie","significant":loss>=100,"loss_cp":loss}
    winning = a["kind"] == "mate" and a["value"] > 0 and not (b["kind"] == "mate" and b["value"]>0)
    losing = b["kind"] == "mate" and b["value"] < 0 and not (a["kind"] == "mate" and a["value"]<0)
    return {"status":"supported_mate_transition" if winning or losing else "mate_distance_unclassified","significant":winning or losing,"mate_transition":winning or losing}


def rank_key(item, source="actual", evidence_id=None):
    frequency = item.get("actual_frequency") if source == "actual" else next((e.get("frequency") for e in item.get("source_evidence",[]) if e.get("kind")==source and e.get("evidence_id")==evidence_id),None)
    engine = item.get("engine") or {}
    return (-item["supporting_game_count"],frequency is None,-(frequency or 0),-int(engine.get("significant",False)),-engine.get("loss_cp",0),item.get("proposal_cost") is None,item.get("proposal_cost") or 0,item["finding_id"])
