/** Consumer authored against public schema artifacts, without Python models. */
import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync, readdirSync} from "node:fs";
import {fileURLToPath} from "node:url";
import path from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import {expectations} from "./expectations.js";
import {assertSemantics} from "./semantics.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ajv = new Ajv2020.default({strict:true, strictRequired:false, discriminator:true});
const producerRoot = process.env.TEMPO_CONTRACT_PRODUCER_OUTPUT;
assert.ok(producerRoot, "Run via contracts/run_tests.py to exchange actual Python producer output");
type Payload = Record<string, unknown>;
function schemaValidator(contract: string, model: string) {
  const schema = JSON.parse(readFileSync(path.join(root, contract, "schemas/v1", model+".schema.json"), "utf8"));
  // Strip Pydantic's discriminator annotation; AJV validates oneOf + const tags.
  const removeDiscriminator = (value: unknown): void => {
    if (Array.isArray(value)) value.forEach(removeDiscriminator);
    else if (value && typeof value === "object") {
      delete (value as Payload).discriminator;
      Object.values(value).forEach(removeDiscriminator);
    }
  };
  removeDiscriminator(schema);
  return ajv.getSchema(schema.$id) ?? ajv.compile(schema);
}
function fixture(contract: string, name: string): Payload {
  return JSON.parse(readFileSync(path.join(root, contract, "fixtures/v1", name+".json"), "utf8")).payload as Payload;
}
function setField(payload: Payload, fields: string[], value: string): void {
  let target = payload;
  for (const field of fields.slice(0, -1)) target = target[field] as Payload;
  target[fields.at(-1)!] = value;
}
function adviceForm(publication: Payload, wrapped: boolean): {model: string, payload: Payload} {
  if (!wrapped) return {model: "AdvisoryPublication", payload: publication};
  const payload = fixture("intelligence", "available");
  payload.publication = publication;
  return {model: "IntelligenceAvailability", payload};
}
let examples = 0;
for (const [contract, cases] of Object.entries(expectations)) {
  const producerRecords = JSON.parse(readFileSync(path.join(producerRoot, contract+".json"), "utf8")) as Array<{case:string, model:string, payload:Record<string, unknown>}>;
  assert.deepEqual(producerRecords.map(record=>record.case).sort(), Object.keys(cases).sort(), "producer/consumer case inventory differs");
  const fixtureNames = readdirSync(path.join(root,contract,"fixtures/v1")).filter(name=>name.endsWith(".json")).map(name=>name.slice(0,-5)).sort();
  assert.deepEqual(fixtureNames,Object.keys(cases).sort(), "unowned or absent consumer example");
  for (const record of producerRecords) {
    const expected = cases[record.case];
    const validate = schemaValidator(contract, expected.model);
    test(`${contract}: Python producer 1.0.0 -> TypeScript consumer 1.0.0 / ${record.case}`,()=> {
      assert.equal(record.model,expected.model);
      assert.ok(validate(record.payload), JSON.stringify(validate.errors));
      assertSemantics(record.model,record.payload);
      assert.equal(record.payload.workspace_id,"workspace-a");
      for (const [field, expectedValue] of Object.entries(expected.checks)) {
        const actual = field.split(".").reduce<unknown>((value, key)=>value === undefined || value === null ? undefined : (value as Record<string,unknown>)[key], record.payload);
        assert.deepEqual(actual,expectedValue,field);
      }
      assert.equal(validate({...record.payload,contract_version:2}),false,"unsupported wire version");
      assert.equal(validate({...record.payload,contract_version:true}),false,"boolean is not version 1");
      assert.equal(validate({...record.payload,internal_database_record:{}}),false,"internal data leakage");
      assert.equal(validate({...record.payload,workspace_id:42}),false,"malformed identity");
    });
    examples++;
  }
}
assert.ok(examples>0,"zero selected TypeScript consumer examples");

for(const filename of readdirSync(path.join(root,"semantic-negative/v1"))) {
  if(!filename.endsWith(".json")) continue;
  const fixture=JSON.parse(readFileSync(path.join(root,"semantic-negative/v1",filename),"utf8")) as {package:string,model:string,payload:Record<string,unknown>};
  test(`consumer rejects semantic negative / ${filename}`,()=> {
    const validate = schemaValidator(fixture.package, fixture.model);
    assert.ok(validate(fixture.payload), JSON.stringify(validate.errors));
    assert.throws(()=>assertSemantics(fixture.model,fixture.payload));
  });
}


// Outcomes are independently specified here, not obtained from a Python validator.
const timestampCases: Array<[string, string, boolean]> = [
  ["no_fraction", "2026-10-14T12:00:00Z", true],
  ["fraction_1", "2026-10-14T12:00:00.1Z", true],
  ["fraction_2", "2026-10-14T12:00:00.12Z", true],
  ["fraction_3", "2026-10-14T12:00:00.123Z", true],
  ["fraction_4", "2026-10-14T12:00:00.1234Z", true],
  ["fraction_5", "2026-10-14T12:00:00.12345Z", true],
  ["fraction_6", "2026-10-14T12:00:00.123456Z", true],
  ["positive_offset", "2026-10-14T12:00:00.000001+02:30", true],
  ["negative_offset", "2026-10-14T12:00:00.120000-05:45", true],
  ["positive_zero_offset", "2026-10-14T12:00:00+00:00", true],
  ["negative_zero_offset", "2026-10-14T12:00:00-00:00", true],
  ["leap_year_2000", "2000-02-29T00:00:00Z", true],
  ["year_0001", "0001-01-01T00:00:00Z", true],
  ["year_0099", "0099-12-31T23:59:59Z", true],
  ["year_9999", "9999-12-31T23:59:59.999999Z", true],
  ["offset_before_year_0001", "0001-01-01T00:00:00+23:59", true],
  ["offset_after_year_9999", "9999-12-31T23:59:59-23:59", true],
  ["invalid_leap_1900", "1900-02-29T00:00:00Z", false],
  ["invalid_leap_2026", "2026-02-29T00:00:00Z", false],
  ["invalid_month", "2026-13-01T00:00:00Z", false],
  ["invalid_day", "2026-04-31T00:00:00Z", false],
  ["year_0000", "0000-01-01T00:00:00Z", false],
  ["offset_plus_minutes_60", "2026-10-14T12:00:00+00:60", false],
  ["offset_minus_minutes_60", "2026-10-14T12:00:00-00:60", false],
  ["offset_minutes_99", "2026-10-14T12:00:00+01:99", false],
  ["offset_hours_24", "2026-10-14T12:00:00+24:00", false],
  ["fraction_7", "2026-10-14T12:00:00.1234567Z", false],
  ["missing_timezone", "2026-10-14T12:00:00", false],
  ["hour_24", "2026-10-14T24:00:00Z", false],
  ["minute_60", "2026-10-14T12:60:00Z", false],
  ["second_60", "2026-10-14T12:00:60Z", false],
  ["lowercase_t", "2026-10-14t12:00:00Z", false],
  ["lowercase_z", "2026-10-14T12:00:00z", false],
  ["trailing_junk", "2026-10-14T12:00:00Zjunk", false],
  ["trailing_newline", "2026-10-14T12:00:00Z\n", false],
  ["leading_space", " 2026-10-14T12:00:00Z", false],
  ["trailing_space", "2026-10-14T12:00:00Z ", false],
];
const timestampEnvelopes: Array<[string, string, string, string[]]> = [
  ["learning", "attempt-issued-choice", "AttemptIssuance", ["attempt", "issued_at"]],
  ["content-publication", "publication", "Publication", ["built_at"]],
  ["game-evidence", "imported-game", "ImportedGame", ["provenance", "imported_at"]],
  ["intelligence", "advice-ranking", "AdvisoryPublication", ["produced_at"]],
  ["analysis-jobs", "cancel-requested", "CancellationAcknowledgement", ["acknowledged_at"]],
];
for (const [contract, name, model, fieldPath] of timestampEnvelopes) {
  for (const [caseName, timestamp, accepted] of timestampCases) {
    test(`repair: ${contract} timestamp validity and wire spelling / ${caseName}`, () => {
      const payload = fixture(contract, name);
      if (contract === "intelligence") payload.valid_until = null;
      setField(payload, fieldPath, timestamp);
      const originalWire = JSON.stringify(payload);
      const validate = schemaValidator(contract, model);
      if (accepted) {
        assert.ok(validate(payload), JSON.stringify(validate.errors));
        assertSemantics(model, payload);
      } else {
        assert.throws(() => {
          assert.ok(validate(payload), JSON.stringify(validate.errors));
          assertSemantics(model, payload);
        });
      }
      assert.equal(JSON.stringify(payload), originalWire, "timestamp wire spelling was rewritten");
    });
  }
}
for (const wrapped of [false, true]) {
  for (const kind of ["ranking", "probabilities", "readiness", "coverage"]) {
    test(`repair: ${wrapped ? "wrapped" : "standalone"} advisory valid control / ${kind}`, () => {
      const {model, payload} = adviceForm(fixture("intelligence", "advice-"+kind), wrapped);
      assert.ok(schemaValidator("intelligence", model)(payload));
      assertSemantics(model, payload);
    });
  }
  const expiryCases: Array<[string, string, boolean]> = [
    ["one_microsecond_later", "2026-10-14T12:00:00.000002Z", true],
    ["same_instant", "2026-10-14T12:00:00.000001Z", false],
    ["one_microsecond_earlier", "2026-10-14T12:00:00.000000Z", false],
    ["offset_equivalent_instant", "2026-10-14T14:00:00.000001+02:00", false],
  ];
  for (const [caseName, expiry, accepted] of expiryCases) {
    test(`repair: ${wrapped ? "wrapped" : "standalone"} advisory microsecond expiry / ${caseName}`, () => {
      const publication = fixture("intelligence", "advice-ranking");
      publication.produced_at = "2026-10-14T12:00:00.000001Z";
      publication.valid_until = expiry;
      const {model, payload} = adviceForm(publication, wrapped);
      const originalWire = JSON.stringify(payload);
      assert.ok(schemaValidator("intelligence", model)(payload));
      if (accepted) assertSemantics(model, payload);
      else assert.throws(() => assertSemantics(model, payload));
      assert.equal(JSON.stringify(payload), originalWire);
    });
  }
}
for (const worker of ["stockfish", "maia"]) {
  const chronologyCases: Array<[string, string, boolean]> = [
    ["one_microsecond_earlier", "2026-10-14T12:00:00.000000Z", false],
    ["same_instant", "2026-10-14T12:00:00.000001Z", true],
    ["one_microsecond_later", "2026-10-14T12:00:00.000002Z", true],
    ["offset_equivalent_instant", "2026-10-14T14:00:00.000001+02:00", true],
  ];
  for (const [caseName, finishedAt, accepted] of chronologyCases) {
    test(`repair: ${worker} execution microsecond chronology / ${caseName}`, () => {
      const payload = fixture("analysis-jobs", worker+"-result");
      const provenance = payload.provenance as Payload;
      provenance.started_at = "2026-10-14T12:00:00.000001Z";
      provenance.finished_at = finishedAt;
      const model = worker === "stockfish" ? "StockfishResult" : "MaiaResult";
      const originalWire = JSON.stringify(payload);
      assert.ok(schemaValidator("analysis-jobs", model)(payload));
      if (accepted) assertSemantics(model, payload);
      else assert.throws(() => assertSemantics(model, payload));
      assert.equal(JSON.stringify(payload), originalWire);
    });
  }
}
test("repair: equivalent timestamp spelling remains distinct serialized advice", () => {
  const first = fixture("intelligence", "advice-ranking");
  const second = fixture("intelligence", "advice-ranking");
  first.produced_at = "2026-10-14T12:00:00.000001Z";
  second.produced_at = "2026-10-14T14:00:00.000001+02:00";
  assertSemantics("AdvisoryPublication", first);
  assertSemantics("AdvisoryPublication", second);
  assert.notEqual(JSON.stringify(first), JSON.stringify(second));
});

for (const wrapped of [false, true]) {
  for (const kind of ["probabilities", "coverage"]) {
    const massCases: Array<[string, number, boolean]> = [["within_positive_tolerance", 0.5e-9, true], ["within_negative_tolerance", -0.5e-9, true], ["outside_tolerance", 2e-9, false]];
    for (const [caseName, deviation, accepted] of massCases) {
      test(`repair: ${wrapped ? "wrapped" : "standalone"} ${kind} normalization tolerance / ${caseName}`, () => {
        const publication = fixture("intelligence", "advice-"+kind);
        const advisory = publication.advisory as Payload;
        if (kind === "probabilities") {
          advisory.moves = [{move: "e2e4", probability: 0.2}];
          advisory.unknown_mass = 0.8 + deviation;
        } else advisory.unknown_mass = 0.5 + deviation;
        const {model, payload} = adviceForm(publication, wrapped);
        assert.ok(schemaValidator("intelligence", model)(payload));
        if (accepted) assertSemantics(model, payload);
        else assert.throws(() => assertSemantics(model, payload));
      });
    }
  }
}
