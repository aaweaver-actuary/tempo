/** Independent consumer checks for invariants beyond exported structural schemas. */
import assert from "node:assert/strict";
type ObjectValue = Record<string,unknown>;
const object = (value:unknown):ObjectValue => value as ObjectValue;
const items = (value:unknown):ObjectValue[] => value as ObjectValue[];
const unique = (values:unknown[]) => assert.equal(new Set(values).size,values.length,"duplicate identity");
const sum = (values:number[]) => values.reduce((total,value)=>total+value,0);
const mass = (values:number[]) => assert.ok(Math.abs(sum(values)-1)<1e-9,"mass must total one");
const same = (first:unknown[], second:unknown[]) => assert.deepEqual(first,second,"identity/lineage mismatch");

function timestamp(value:string):void {
  const matches = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  assert.ok(matches,"invalid timestamp");
  const [year,month,day,hour,minute,second] = matches.slice(1,7).map(Number);
  const leap = year%4===0 && (year%100!==0 || year%400===0);
  const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  assert.ok(year>=1 && month>=1 && month<=12 && day>=1 && day<=days[month-1] && hour<=23 && minute<=59 && second<=59,"invalid calendar time");
  if(matches[7]) assert.ok(Number(matches[7])<=23 && Number(matches[8])<=59,"invalid timezone offset");
}

function nestedChecks(value:unknown):void {
  if(Array.isArray(value)) value.forEach(nestedChecks);
  else if(value && typeof value === "object") {
    const record=object(value);
    for(const [key,child] of Object.entries(record)) {
      if(child!==null && typeof child === "string" && (key.endsWith("_at") || key==="valid_until" || key==="next_due")) timestamp(child);
      nestedChecks(child);
    }
    if(record.type === "square_set" && "required" in record) unique([...(record.required as string[]),...(record.optional as string[])]);
    if(record.type === "knight_path" && "minimum_hops" in record) assert.ok((record.minimum_hops as number)<=(record.maximum_hops as number));
    if(record.type === "choice" && "options" in record) {
      const optionIds=items(record.options).map(option=>option.id);
      unique(optionIds);unique(record.correct_option_ids as string[]);
      for(const identity of record.correct_option_ids as string[]) assert.ok(optionIds.includes(identity),"unknown correct option");
    }
  }
}

export function assertSemantics(model:string,payload:ObjectValue):void {
  nestedChecks(payload);
  switch(model) {
    case "ReviewSettlement": {
      const settlement=object(payload.settlement);
      if(settlement.status==="committed") assert.equal(object(settlement.receipt).submission_id,payload.submission_id,"unrelated receipt");
      break;
    }
    case "QueueIssuance": {
      const attempts=items(payload.attempts);
      assert.equal(payload.status==="issued",attempts.length>0,"false queue issuance");
      if(payload.status==="accepted_pending" || payload.status==="retryable_failure") assert.notEqual(payload.reason,null);
      unique(attempts.map(attempt=>attempt.attempt_id));break;
    }
    case "Publication": {
      const definitions=items(payload.definitions);
      unique(definitions.map(definition=>definition.card_id));
      for(const definition of definitions) same([definition.publication_id,definition.publication_generation,definition.source_revision],[payload.publication_id,payload.publication_generation,payload.source_revision]);
      break;
    }
    case "PublicationValidation":assert.equal(payload.status==="validated",(payload.errors as string[]).length===0);break;
    case "RetirementRequest":
      assert.equal(payload.replacement_publication_id===null,payload.replacement_generation===null);
      assert.ok(payload.replacement_publication_id!==payload.publication_id || payload.replacement_generation!==payload.publication_generation,"self replacement");break;
    case "RetirementAcknowledgement":assert.equal(payload.status==="retired",payload.stale_attempts_fenced);
      if(["rejected","retryable_failure","stale_generation"].includes(payload.status as string)) assert.notEqual(payload.reason,null);break;
    case "PublicationAcceptance":if(payload.status!=="accepted_pending") assert.notEqual(payload.reason,null);break;
    case "ActivationAcknowledgement":if(["rejected","retryable_failure","stale_generation"].includes(payload.status as string)) assert.notEqual(payload.reason,null);break;
    case "EvidenceDelivery": {
      const encounters=items(payload.encounters);const identities=encounters.map(encounter=>encounter.encounter_id);unique(identities);
      for(const observation of [...items(payload.comparisons),...items(payload.misses)]) assert.ok(identities.includes(observation.encounter_id),"unobserved encounter");
      unique(items(payload.misses).map(miss=>miss.miss_id));unique(items(payload.obligations).map(obligation=>obligation.obligation_id));
      for(const obligation of items(payload.obligations)) same([obligation.source_evidence_id,obligation.source_revision],[payload.evidence_id,payload.evidence_revision]);break;
    }
    case "EvidenceAcknowledgement":assert.equal(payload.status==="completed",payload.learning_revision!==null);unique(payload.accepted_obligation_ids as string[]);
      if(["retryable_failure","terminal_failure","conflict","stale_revision"].includes(payload.status as string)) assert.notEqual(payload.reason,null);break;
    case "AdvisoryPublication": {
      if(payload.valid_until!==null) assert.ok(Date.parse(payload.valid_until as string)>Date.parse(payload.produced_at as string));
      const advisory=object(payload.advisory);
      if(advisory.kind==="probabilities") {
        const moves=items(advisory.moves);unique(moves.map(move=>move.move));
        mass([sum(moves.filter(move=>move.probability!==null).map(move=>move.probability as number)),advisory.unknown_mass as number]);
        if(moves.some(move=>move.probability===null)) assert.ok((advisory.unknown_mass as number)>0);
      } else if(advisory.kind==="ranking") unique(items(advisory.cards).map(card=>card.card_id));
      else if(advisory.kind==="coverage_recommendation") mass([advisory.known_covered_mass,advisory.known_uncovered_mass,advisory.unknown_mass] as number[]);
      break;
    }
    case "IntelligenceAvailability":
      assert.equal(payload.status==="available",payload.publication!==null);
      if(payload.publication!==null) assert.equal(payload.workspace_id,object(payload.publication).workspace_id);
      else assert.notEqual(payload.reason,null);break;
    case "JobStatus":if(payload.retryable) assert.equal(payload.status,"failed");if(payload.status==="failed") assert.notEqual(payload.reason,null);break;
    case "StockfishResult": {
      const variations=items(payload.variations);
      same(variations.map(variation=>variation.rank),variations.map((_,index)=>index+1));break;
    }
    case "MaiaJob":unique(payload.legal_moves as string[]);break;
    case "MaiaResult": {
      const predictions=items(payload.predictions);unique(predictions.map(prediction=>prediction.move));mass(predictions.map(prediction=>prediction.probability as number));break;
    }
  }
  if("provenance" in payload && !Array.isArray(payload.provenance)) {
    const provenance=object(payload.provenance);
    if("finished_at" in provenance && "started_at" in provenance) assert.ok(Date.parse(provenance.finished_at as string)>=Date.parse(provenance.started_at as string),"execution time reversed");
  }
}
