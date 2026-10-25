import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { planLauncherTransition } from "../tempo-cutover-plan.mjs";

const fixture = name => JSON.parse(readFileSync(new URL(`./fixtures/tempo-cutover-plan/${name}.json`, import.meta.url), "utf8"));
const input = () => fixture("publication-input");
const codes = result => result.blockers.map(blocker => blocker.code);
const both = (value, change) => { change(value.proposed.registration.cutover); change(value.proposed.receipt.cutover); };

test("synthetic publication golden preserves originals and keeps the original maintenance lock identity", () => {
  const supplied = input();
  const before = structuredClone(supplied);
  const result = planLauncherTransition(supplied);
  assert.deepEqual(result, fixture("publication-golden"));
  assert.deepEqual(supplied, before);
  assert.notEqual(result.plan.sourceIdentity, result.plan.targetIdentity);
  assert.equal(result.plan.lockIdentity, result.plan.sourceIdentity);
  assert.notEqual(result.plan.registration, supplied.proposed.registration);
  assert.notEqual(result.plan.original.registration, supplied.current.registration);
  assert(Object.isFrozen(result.plan.registration.cutover.images));
  assert.throws(() => { result.plan.original.registration.root = "/changed"; }, TypeError);
  assert(!Object.hasOwn(result.plan, "activationAllowed"));
});

test("missing accepted-transfer contract blocks final publication without inventing acceptance", () => {
  const supplied = input();
  delete supplied.transferAcceptance;
  const result = planLauncherTransition(supplied);
  assert.equal(result.status, "blocked");
  assert.equal(result.plan, null);
  assert.deepEqual(codes(result), ["accepted-transfer-contract-pending"]);
});

test("an opaque fixture acceptance object is copied without being interpreted as real-history proof", () => {
  const supplied = input();
  supplied.transferAcceptance = { sourcePacket: "comparison-only-fixture", accepted: false };
  const result = planLauncherTransition(supplied);
  assert.equal(result.status, "planned");
  assert.deepEqual(result.plan.transferAcceptance, supplied.transferAcceptance);
  // The separate release gate MUST reject this evidence. Planning establishes
  // correspondence only, so no readiness/approval fields can be minted here.
  assert.equal(result.plan.transferAcceptance.accepted, false);
  assert(!Object.hasOwn(result.plan, "ready"));
});

test("registration-only publication mismatch blocks rather than choosing an authoritative record", () => {
  const supplied = input();
  supplied.proposed.receipt.cutover.workspace = "different-fixture-workspace";
  assert.deepEqual(codes(planLauncherTransition(supplied)), ["publication_identity_mismatch"]);
});

for (const [name, mutate, expected] of [
  ["mutable image tags", value => both(value, identity => { identity.images.web = "fixture-web:latest"; }), "images_unpinned"],
  ["abbreviated revisions", value => both(value, identity => { identity.revision = "bbbbbbb"; }), "revision_unpinned"],
  ["activation begun before publication", value => both(value, identity => { identity.phase = "activation-started"; }), "publication_phase_invalid"],
  ["legacy service mixture", value => both(value, identity => { identity.images.redis = "sha256:" + "5".repeat(64); }), "legacy_service_mixture"],
  ["missing database identity", value => both(value, identity => { delete identity.database; }), "authority_identity_missing"],
  ["missing ingress identity", value => both(value, identity => { delete identity.ingress; }), "composition_identity_missing"],
  ["receipt image drift", value => { value.proposed.receipt.images.web = "sha256:" + "6".repeat(64); }, "receipt_identity_mismatch"],
  ["target port authority drift", value => { value.proposed.registration.webUrl = "http://127.0.0.1:3001"; }, "registration_identity_mismatch"],
  ["different Docker daemon", value => { value.proposed.registration.daemonId = "other-fixture-daemon"; }, "daemon_change_unqualified"],
  ["disposable target promotion", value => { value.proposed.registration.disposable = false; }, "disposable_authority_mismatch"],
  ["unqualified PostgreSQL major upgrade", value => { value.proposed.registration.postgresMajor = 19; }, "postgres_major_change_unqualified"],
  ["mutable PostgreSQL image", value => { value.proposed.registration.postgresImage = "postgres:18"; }, "postgres_image_unpinned"],
  ["checkout-owned secret configuration", value => { value.proposed.registration.envFile = "/fixture/release/learning.env"; }, "private_configuration_in_checkout"],
  ["literal secret material", value => { value.proposed.registration.token = "fixture-secret-never-publish"; }, "secret_material_forbidden"],
  ["runtime token environment literal", value => { value.proposed.registration.TEMPO_LEARNING_TOKEN = "fixture-secret-never-publish"; }, "secret_material_forbidden"],
  ["credentials inside a URL", value => { value.proposed.receipt.note = "postgresql://fixture:fixture-secret@fixture/db"; }, "secret_material_forbidden"],
  ["fresh-install substitution", value => { value.current.registration.version = 2; }, "existing_v1_required"],
]) {
  test(`publication rejects ${name}`, () => {
    const supplied = input();
    mutate(supplied);
    const result = planLauncherTransition(supplied);
    assert.equal(result.status, "blocked");
    assert.equal(result.plan, null);
    assert(codes(result).includes(expected), JSON.stringify(codes(result)));
    assert(!JSON.stringify(result).includes("fixture-secret"));
  });
}

test("legacy source and backup volumes cannot be rebound even with matching candidate records", () => {
  const supplied = input();
  const registration = supplied.proposed.registration;
  registration.volumes[registration.postgresVolumeKey].name = "fixture-legacy-data";
  both(supplied, identity => {
    identity.volumes[registration.postgresVolumeKey].name = "fixture-legacy-data";
    identity.database.volume = "fixture-legacy-data";
  });
  assert.deepEqual(codes(planLauncherTransition(supplied)), ["preserved_volume_rebinding"]);
});

test("property order does not create a false disagreement between equivalent publication identities", () => {
  const supplied = input();
  supplied.proposed.receipt.cutover = Object.fromEntries(Object.entries(supplied.proposed.receipt.cutover).reverse());
  assert.equal(planLauncherTransition(supplied).status, "planned");
});

test("several independently missing inputs remain explicit blockers", () => {
  assert.deepEqual(codes(planLauncherTransition({})), ["transition_identity_missing", "existing_v1_required", "source_identity_missing",
    "candidate_v2_required", "target_identity_missing", "publication_identity_mismatch", "accepted-transfer-contract-pending"]);
});

test("non-JSON input cannot silently alter the preserved prior records", () => {
  for (const value of [null, [], { value: undefined }, { value: Infinity }, { value: () => 1 }, { value: new Date() }]) {
    assert.deepEqual(codes(planLauncherTransition(value)), ["invalid_input"]);
  }
  const cyclic = input();
  cyclic.current.receipt.cycle = cyclic;
  assert.deepEqual(codes(planLauncherTransition(cyclic)), ["invalid_input"]);
  assert.deepEqual(codes(planLauncherTransition({ value: Array(1) })), ["invalid_input"]);
  const getter = { get value() { throw new Error("Getter must not execute"); } };
  assert.deepEqual(codes(planLauncherTransition(getter)), ["invalid_input"]);
});
