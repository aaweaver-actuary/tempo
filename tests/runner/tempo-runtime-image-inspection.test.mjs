import assert from "node:assert/strict";
import { test } from "node:test";
import { inspectImmutableImageSet } from "../../scripts/tempo-runtime.mjs";
const id = n => `sha256:${String(n).repeat(64)}`;
const image = n => ({ Id: id(n), Config: { Labels: { "org.opencontainers.image.revision": "revision" } } });

test("immutable image checkpoint batches distinct IDs and maps shuffled results", async () => {
  const calls = [];
  const result = await inspectImmutableImageSet(async args => { calls.push(args); return { code: 0, stdout: JSON.stringify([image(2), image(1)]) }; }, [id(1), id(1), id(2)]);
  assert.deepEqual(calls, [["image", "inspect", id(1), id(2)]]);
  assert.equal(result.get(id(1)).Id, id(1)); assert.equal(result.get(id(2)).Id, id(2));
});
test("immutable image checkpoint rejects missing, duplicate, malformed, foreign and failed output", async () => {
  for (const result of [
    { code: 0, stdout: JSON.stringify([image(1)]) },
    { code: 0, stdout: JSON.stringify([image(1), image(1)]) },
    { code: 0, stdout: "{" },
    { code: 0, stdout: JSON.stringify([image(1), image(3)]) },
    { code: 1, stdout: JSON.stringify([image(1), image(2)]) },
  ]) await assert.rejects(inspectImmutableImageSet(async () => result, [id(1), id(2)]));
  await assert.rejects(inspectImmutableImageSet(async () => { throw new Error("daemon unavailable"); }, [id(1)]), /daemon unavailable/);
  await assert.rejects(inspectImmutableImageSet(async () => ({}), ["mutable-tag"]), /invalid/);
});
test("immutable image availability is freshly checked at each checkpoint", async () => {
  let calls = 0;
  const docker = async () => ++calls === 1 ? { code: 0, stdout: JSON.stringify([image(1)]) } : { code: 1, stdout: "[]" };
  await inspectImmutableImageSet(docker, [id(1), id(1)]);
  await assert.rejects(inspectImmutableImageSet(docker, [id(1)]), /failed/);
  assert.equal(calls, 2);
});
