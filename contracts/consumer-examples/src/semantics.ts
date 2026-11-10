/** Independent consumer checks for invariants beyond exported structural schemas. */
import assert from "node:assert/strict";
type ObjectValue = Record<string,unknown>;
const object = (value:unknown):ObjectValue => value as ObjectValue;
const items = (value:unknown):ObjectValue[] => value as ObjectValue[];
const unique = (values:unknown[]) => assert.equal(new Set(values).size,values.length,"duplicate identity");
const sum = (values:number[]) => values.reduce((total,value)=>total+value,0);
const mass = (values:number[]) => assert.ok(Math.abs(sum(values)-1)<=1e-9,"mass must total one");
const same = (first:unknown[], second:unknown[]) => assert.deepEqual(first,second,"identity/lineage mismatch");

/** Exact Gregorian instant; the original wire spelling is never rewritten. */
function timestampMicroseconds(value: string): bigint {
  const matches = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  assert.ok(matches && matches[0] === value, "invalid timestamp");
  const [year, month, day, hour, minute, second] = matches.slice(1, 7).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  assert.ok(year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= monthDays[month - 1] && hour <= 23 && minute <= 59 && second <= 59, "invalid calendar time");
  const offsetHours = Number(matches[9] ?? 0);
  const offsetMinutes = Number(matches[10] ?? 0);
  assert.ok(offsetHours <= 23 && offsetMinutes <= 59, "invalid timezone offset");
  const precedingYears = year - 1;
  const precedingDays = precedingYears * 365 + Math.floor(precedingYears / 4) - Math.floor(precedingYears / 100) + Math.floor(precedingYears / 400)
    + sum(monthDays.slice(0, month - 1)) + day - 1;
  const seconds = precedingDays * 86400 + hour * 3600 + minute * 60 + second;
  const fraction = BigInt((matches[7] ?? "").padEnd(6, "0"));
  const offsetSign = matches[8] === "-" ? -1n : 1n;
  const offset = BigInt(offsetHours * 3600 + offsetMinutes * 60) * 1_000_000n * offsetSign;
  return BigInt(seconds) * 1_000_000n + fraction - offset;
}

function nestedChecks(value:unknown):void {
  if(Array.isArray(value)) value.forEach(nestedChecks);
  else if(value && typeof value === "object") {
    const record=object(value);
    for(const [key,child] of Object.entries(record)) {
      if(child!==null && typeof child === "string" && (key.endsWith("_at") || key==="valid_until" || key==="next_due")) timestampMicroseconds(child);
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
    case "EvidenceDelivery": {
      const encounters=items(payload.encounters);const identities=encounters.map(encounter=>encounter.encounter_id);unique(identities);
      for(const observation of [...items(payload.comparisons),...items(payload.misses)]) assert.ok(identities.includes(observation.encounter_id),"unobserved encounter");
      unique(items(payload.misses).map(miss=>miss.miss_id));unique(items(payload.obligations).map(obligation=>obligation.obligation_id));
      for(const obligation of items(payload.obligations)) same([obligation.source_evidence_id,obligation.source_revision],[payload.evidence_id,payload.evidence_revision]);break;
    }
    case "EvidenceAcknowledgement":assert.equal(payload.status==="completed",payload.learning_revision!==null);unique(payload.accepted_obligation_ids as string[]);
      if(["retryable_failure","terminal_failure","conflict","stale_revision"].includes(payload.status as string)) assert.notEqual(payload.reason,null);break;
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
    if("finished_at" in provenance && "started_at" in provenance) assert.ok(timestampMicroseconds(provenance.finished_at as string)>=timestampMicroseconds(provenance.started_at as string),"execution time reversed");
  }
}
