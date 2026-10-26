// Source-only common-path acceptance. Production implementations are real;
// only command/HTTP transport and explicit admission protocol observations are fake.
import assert from "node:assert/strict";
import test from "node:test";
import { main } from "../../scripts/tempo-cli.mjs";
import { createStudyStartupFixture } from "./fixtures/tempo-study-startup-fixture.mjs";

for (const command of ["start", "stop", "status"])
  test(`minimal Study startup ${command} composes the real public leaves through normal CLI`, async t => {
    const f = await createStudyStartupFixture(t, { command }), messages = [];
    assert.equal(f.registration.installedStudy.stateDirectory, f.admission.directory);
    assert.equal(f.registration.installedStudy.lockIdentity, f.admission.plan.sourceIdentity);
    const invoke = () => main([command, "--config", f.admission.registrationPath], message => messages.push(message),
      { studyAssemblyDependencies: f.dependencies });
    assert.equal(await invoke(), 0);
    const ups = f.calls.filter(call => call.argv.includes("up")), stops = f.calls.filter(call => call.argv.includes("stop"));
    if (command === "start") {
      assert.equal(ups.length, 2); assert.equal(ups[0].argv.at(-1), "postgres");
      assert.deepEqual(ups[1].argv.slice(-2), ["learning", "study-web"]);
      assert.ok(ups.every(call => call.lockHeld), "Both service effects retain the original target lock");
      const postgresStart = f.calls.indexOf(ups[0]), applicationStart = f.calls.indexOf(ups[1]);
      assert.ok(f.calls.slice(0, postgresStart).some(call => call.lockHeld && call.argv.some(arg => String(arg).includes("pg_controldata"))),
        "Existing physical cluster identity is read before PostgreSQL can start");
      assert.ok(f.calls.slice(0, postgresStart).some(call => call.lockHeld
        && call.argv.includes("tempo_learning_database.runtime_readback") && call.argv.includes("--help")),
        "The selected image supplies the public database verifier before PostgreSQL starts");
      const databaseReadback = f.calls.findIndex(call => call.argv.includes("--expected-database"));
      assert.ok(postgresStart < databaseReadback && databaseReadback < applicationStart,
        "Public database identity/schema readback follows PostgreSQL and precedes application start");
      assert.equal(f.fetchCalls.length, 1);
    } else if (command === "stop") {
      assert.equal(ups.length, 0); assert.equal(stops.length, 1);
      assert.deepEqual(stops[0].argv.slice(-2), ["study-web", "learning"]);
      assert.equal(stops[0].lockHeld, true);
      assert.equal(f.containers.postgres.State.Running, true, "Stopping Study preserves PostgreSQL and its data owner");
    } else {
      assert.equal(ups.length, 0); assert.equal(stops.length, 0);
      assert.equal(f.calls.some(call => call.argv[2] === "run" || call.lockHeld), false);
      assert.ok(messages.some(message => message.includes("runtime: cold") && message.includes("schema unverified")
        && message.includes("health unverified") && message.includes("PostgreSQL stopped") && message.includes("Learning stopped")),
        "Cold metadata is reported without pretending to verify live schema or health");
      for (const container of Object.values(f.containers)) container.State.Running = true;
      messages.length = 0;
      assert.equal(await invoke(), 0);
      assert.ok(messages.some(message => message.includes("runtime: running") && message.includes("schema unverified")
        && message.includes("health unverified") && message.includes("PostgreSQL running") && message.includes("Learning running")),
        "Current running identity is read through the actual runtime public boundary without claiming live schema or health");
      assert.equal(f.calls.some(call => call.argv.includes("up") || call.argv.includes("stop") || call.lockHeld), false);
    }
    assert.equal(f.calls.some(call => call.argv.some(arg => /^(?:build|pull|migrate|grant|DROP)$/.test(arg))), false);
  });
