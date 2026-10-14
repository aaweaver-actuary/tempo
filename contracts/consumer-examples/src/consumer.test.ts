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
let examples = 0;
for (const [contract, cases] of Object.entries(expectations)) {
  const producerRecords = JSON.parse(readFileSync(path.join(producerRoot, contract+".json"), "utf8")) as Array<{case:string, model:string, payload:Record<string, unknown>}>;
  assert.deepEqual(producerRecords.map(record=>record.case).sort(), Object.keys(cases).sort(), "producer/consumer case inventory differs");
  const fixtureNames = readdirSync(path.join(root,contract,"fixtures/v1")).filter(name=>name.endsWith(".json")).map(name=>name.slice(0,-5)).sort();
  assert.deepEqual(fixtureNames,Object.keys(cases).sort(), "unowned or absent consumer example");
  for (const record of producerRecords) {
    const expected = cases[record.case];
    const schema = JSON.parse(readFileSync(path.join(root,contract,"schemas/v1",expected.model+".schema.json"),"utf8"));
    // Pydantic's discriminator annotation is not an AJV discriminator schema;
    // ordinary oneOf + const tags validates the same exported union shape.
    const removeDiscriminator = (value:unknown):void => {
      if (Array.isArray(value)) value.forEach(removeDiscriminator);
      else if (value && typeof value === "object") {
        delete (value as Record<string,unknown>).discriminator;
        Object.values(value).forEach(removeDiscriminator);
      }
    };
    removeDiscriminator(schema);
    const validate = ajv.getSchema(schema.$id) ?? ajv.compile(schema);
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
  const fixture=JSON.parse(readFileSync(path.join(root,"semantic-negative/v1",filename),"utf8")) as {model:string,payload:Record<string,unknown>};
  test(`consumer rejects semantic negative / ${filename}`,()=>assert.throws(()=>assertSemantics(fixture.model,fixture.payload)));
}
