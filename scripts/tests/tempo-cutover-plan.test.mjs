import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { planLauncherFreshStart, readGraphTransferVerification, readFreshInitializationVerification } from "../tempo-cutover-plan.mjs";

const fixture = name => JSON.parse(readFileSync(new URL(`./fixtures/tempo-cutover-plan/${name}.json`, import.meta.url), "utf8"));
const input = () => fixture("publication-input");
const codes = result => result.blockers.map(blocker => blocker.code);
const both = (value, change) => { change(value.proposed.registration.cutover); change(value.proposed.receipt.cutover); };

test("synthetic publication golden preserves originals and keeps the original maintenance lock identity", () => {
  const supplied = input();
  const before = structuredClone(supplied);
  const result = planLauncherFreshStart(supplied);
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

test("missing graph verification blocks fresh-start publication without inventing graph acceptance", () => {
  const supplied = input();
  delete supplied.graphVerification;
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "blocked");
  assert.equal(result.plan, null);
  assert.deepEqual(codes(result), ["graph-contract-pending"]);
});

test("fresh start requires explicit operator choice and public graph/source input", () => {
  const supplied = input();
  delete supplied.freshStartChoice;
  delete supplied.graphSource;
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["fresh_start_choice_required", "graph-contract-pending"]);
});

test("fresh start requires explicit final clean-Learning initialization input", () => {
  const supplied = input();
  delete supplied.freshInitialization;
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["fresh-initialization-contract-pending"]);
});

test("initial null receipt placeholders cannot reach final publication planning", () => {
  const supplied = input();
  both(supplied, identity => { identity.graphReceipt = null; identity.freshInitReceipt = null; });
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["graph-contract-pending", "fresh-initialization-contract-pending"]);
});

test("fresh-start structural planning has no old-history, background, pending-work or backup preconditions", () => {
  const supplied = input();
  delete supplied.current.receipt.backup;
  delete supplied.proposed.registration.volumes["learning-backups"];
  both(supplied, identity => { delete identity.volumes["learning-backups"]; });
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "planned");
  assert.equal(result.plan.freshStartChoice, "graph-preserving-fresh-start");
  assert(!Object.hasOwn(result.plan, "transferAcceptance"));
  assert(!Object.hasOwn(result.plan.registration.cutover, "transferReceipt"));
  assert(!Object.hasOwn(result.plan.registration.cutover, "authorityEpoch"));
});

test("known prior deployment receipt absence is preserved without requiring historical evidence", () => {
  const supplied = input();
  supplied.current.receipt = null;
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "planned");
  assert.equal(result.plan.original.receipt, null);
  assert(Object.hasOwn(result.plan.original, "receipt"));
});

test("missing prior receipt property remains unknown rather than silently becoming absent", () => {
  const supplied = input();
  delete supplied.current.receipt;
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["prior_publication_unknown"]);
});

test("actual graph and fresh-init readers fail closed even for truthy packets claiming acceptance", () => {
  const packet = { accepted: true, fixtureOnly: false, reference: "unverified-claim" };
  const graph = readGraphTransferVerification(packet);
  const fresh = readFreshInitializationVerification(packet);
  assert.equal(graph.status, "blocked");
  assert.equal(fresh.status, "blocked");
  assert.equal(graph.verification, null);
  assert.equal(fresh.verification, null);
  assert.deepEqual(codes(graph), ["graph-contract-pending"]);
  assert.deepEqual(codes(fresh), ["fresh-initialization-contract-pending"]);
});

test("opaque graph evidence is copied without interpreting it as actual graph proof", () => {
  const supplied = input();
  supplied.graphVerification = { sourcePacket: "comparison-only-fixture", accepted: false };
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "planned");
  assert.deepEqual(result.plan.graphVerification, supplied.graphVerification);
  // The separate release gate MUST reject this evidence. Planning establishes
  // correspondence only, so no readiness/approval fields can be minted here.
  assert.equal(result.plan.graphVerification.accepted, false);
  assert(!Object.hasOwn(result.plan, "ready"));
});

test("registration-only publication mismatch blocks rather than choosing an authoritative record", () => {
  const supplied = input();
  supplied.proposed.receipt.cutover.workspace = "different-fixture-workspace";
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["publication_identity_mismatch"]);
});

for (const [name, mutate, expected] of [
  ["mutable image tags", value => both(value, identity => { identity.images["study-web"] = "fixture-web:latest"; }), "images_unpinned"],
  ["abbreviated revisions", value => both(value, identity => { identity.revision = "bbbbbbb"; }), "revision_unpinned"],
  ["activation begun before publication", value => both(value, identity => { identity.phase = "activation-started"; }), "publication_phase_invalid"],
  ["legacy service mixture", value => both(value, identity => { identity.images.redis = "sha256:" + "5".repeat(64); }), "legacy_service_mixture"],
  ["missing database identity", value => both(value, identity => { delete identity.database; }), "authority_identity_missing"],
  ["missing ingress identity", value => both(value, identity => { delete identity.ingress; }), "composition_identity_missing"],
  ["receipt image drift", value => { value.proposed.receipt.images["study-web"] = "sha256:" + "6".repeat(64); }, "receipt_identity_mismatch"],
  ["target port authority drift", value => { value.proposed.registration.webUrl = "http://127.0.0.1:3001"; }, "registration_identity_mismatch"],
  ["different Docker daemon", value => { value.proposed.registration.daemonId = "other-fixture-daemon"; }, "daemon_change_unqualified"],
  ["disposable target promotion", value => { value.proposed.registration.disposable = false; }, "disposable_authority_mismatch"],
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
    const result = planLauncherFreshStart(supplied);
    assert.equal(result.status, "blocked");
    assert.equal(result.plan, null);
    assert(codes(result).includes(expected), JSON.stringify(codes(result)));
    assert(!JSON.stringify(result).includes("fixture-secret"));
  });
}

for (const service of ["study-web", "learning", "postgres"]) {
  test(`publication rejects missing required ${service} artifact even when all candidate records agree`, () => {
    const supplied = input();
    both(supplied, identity => { delete identity.images[service]; });
    delete supplied.proposed.receipt.images[service];
    assert(codes(planLauncherFreshStart(supplied)).includes("required_artifact_missing"));
  });
}

test("PostgreSQL alone cannot satisfy the Study publication artifact contract", () => {
  const supplied = input();
  both(supplied, identity => { delete identity.images["study-web"]; delete identity.images.learning; });
  delete supplied.proposed.receipt.images["study-web"];
  delete supplied.proposed.receipt.images.learning;
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "blocked");
  assert(codes(result).includes("required_artifact_missing"));
});

test("legacy web service aliases cannot substitute for the existing study-web interface", () => {
  const supplied = input();
  both(supplied, identity => { identity.images.web = identity.images["study-web"]; delete identity.images["study-web"]; });
  supplied.proposed.receipt.images.web = supplied.proposed.receipt.images["study-web"];
  delete supplied.proposed.receipt.images["study-web"];
  supplied.proposed.registration.ports[0].service = "web";
  const result = planLauncherFreshStart(supplied);
  assert(codes(result).includes("required_artifact_missing"));
  assert(codes(result).includes("ingress_service_mismatch"));
});

test("a selected Learning artifact cannot impersonate the Study ingress port service", () => {
  const supplied = input();
  supplied.proposed.registration.ports[0].service = "learning";
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["ingress_service_mismatch"]);
});

test("published ports must name selected artifact services", () => {
  const supplied = input();
  supplied.proposed.registration.ports.push({ service: "unselected-fixture", host: "127.0.0.1", port: "3001", target: 80 });
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["port_service_unselected"]);
});

test("Study ingress port must match both browser authority and the existing ingress listener", () => {
  for (const change of [{ host: "0.0.0.0" }, { port: "3001" }, { target: 8001 }]) {
    const supplied = input();
    Object.assign(supplied.proposed.registration.ports[0], change);
    assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["ingress_service_mismatch"]);
  }
});

test("PostgreSQL identity is matched to its own service rather than any artifact with the same image", () => {
  const supplied = input();
  const change = images => { images["study-web"] = supplied.proposed.registration.postgresImage; images.postgres = "sha256:" + "6".repeat(64); };
  both(supplied, identity => change(identity.images));
  change(supplied.proposed.receipt.images);
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["postgres_image_mismatch"]);
});

test("an independent clean target can explicitly select a different PostgreSQL major without upgrading the legacy cluster", () => {
  const supplied = input();
  const image = "sha256:" + "9".repeat(64);
  supplied.proposed.registration.postgresMajor = 19;
  supplied.proposed.registration.postgresImage = image;
  both(supplied, identity => { identity.images.postgres = image; });
  supplied.proposed.receipt.images.postgres = image;
  const result = planLauncherFreshStart(supplied);
  assert.equal(result.status, "planned");
  assert.equal(result.plan.registration.postgresMajor, 19);
  assert.equal(result.plan.original.registration.postgresMajor, 18);
  assert.equal(supplied.current.registration.postgresMajor, 18);
  // This is structural selection only. Actual new-target/image compatibility
  // remains the public fresh-init provider's obligation, not an inferred pass.
  assert.deepEqual(codes(readFreshInitializationVerification(result.plan.freshInitialization)), ["fresh-initialization-contract-pending"]);
});

test("the independent fresh target still requires an explicit valid PostgreSQL major", () => {
  for (const value of [null, 0, -1, "19"]) {
    const supplied = input();
    supplied.proposed.registration.postgresMajor = value;
    assert(codes(planLauncherFreshStart(supplied)).includes("target_identity_missing"));
  }
  const supplied = input();
  delete supplied.proposed.registration.postgresMajor;
  assert(codes(planLauncherFreshStart(supplied)).includes("target_identity_missing"));
});

test("legacy source and backup volumes cannot be rebound even with matching candidate records", () => {
  const supplied = input();
  const registration = supplied.proposed.registration;
  registration.volumes[registration.postgresVolumeKey].name = "fixture-legacy-data";
  both(supplied, identity => {
    identity.volumes[registration.postgresVolumeKey].name = "fixture-legacy-data";
    identity.database.volume = "fixture-legacy-data";
  });
  assert.deepEqual(codes(planLauncherFreshStart(supplied)), ["preserved_volume_rebinding"]);
});

test("property order does not create a false disagreement between equivalent publication identities", () => {
  const supplied = input();
  supplied.proposed.receipt.cutover = Object.fromEntries(Object.entries(supplied.proposed.receipt.cutover).reverse());
  assert.equal(planLauncherFreshStart(supplied).status, "planned");
});

test("several independently missing inputs remain explicit blockers", () => {
  assert.deepEqual(codes(planLauncherFreshStart({})), ["transition_identity_missing", "existing_v1_required", "prior_publication_unknown", "source_identity_missing",
    "candidate_v2_required", "target_identity_missing", "publication_identity_mismatch", "fresh_start_choice_required", "graph-contract-pending", "fresh-initialization-contract-pending"]);
});

test("non-JSON input cannot silently alter the preserved prior records", () => {
  for (const value of [null, [], { value: undefined }, { value: Infinity }, { value: () => 1 }, { value: new Date() }]) {
    assert.deepEqual(codes(planLauncherFreshStart(value)), ["invalid_input"]);
  }
  const cyclic = input();
  cyclic.current.receipt.cycle = cyclic;
  assert.deepEqual(codes(planLauncherFreshStart(cyclic)), ["invalid_input"]);
  assert.deepEqual(codes(planLauncherFreshStart({ value: Array(1) })), ["invalid_input"]);
  const getter = { get value() { throw new Error("Getter must not execute"); } };
  assert.deepEqual(codes(planLauncherFreshStart(getter)), ["invalid_input"]);
});
