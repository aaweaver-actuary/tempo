import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { checkComponents, repository, validateManifest, compositionPaths, validateCompositionOwnership, censusPaths, validateCensusOwnership, validateCensusInterfaces, validateStartupFacts, validateDdlResources, validateReductionTools, validateContractTooling, validateDatabaseCliTools, validateBackupVerificationSources, validateManagedCutSources } from "../check-components.mjs";
import { validateOwnedPaths, selectComponents } from "../selection.mjs";
import { validateMigrationInventory } from "../../../database/tooling/check-migration-inventory.mjs";

const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3.12";
const original = JSON.parse(readFileSync(join(repository, "components.json")));
test("T02 pure package tracked sources have exactly one declared owner", () => {
  const result = spawnSync("git", ["ls-files", "packages/board-ui/src", "packages/evidence-math/src"], { cwd: repository, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const paths = result.stdout.trim().split("\n").filter(Boolean);
  assert.ok(paths.length);
  validateOwnedPaths(original, paths, ["board-ui", "evidence-math"]);
});
test("T02 mapped browser and CLI sources have exactly one explicit owner", () => {
  const paths = compositionPaths(repository);
  assert.ok(["tempo", "tempo-restart.sh", "public/sw.js", "app/page.tsx", "static/src/main.tsx", "vite.static.config.ts", "tools/component-build/check-components.mjs"].every(path => paths.includes(path)));
  validateCompositionOwnership(original, paths);
  for (const id of ["study-web", "tempo-cli", "integration"]) {
    const component = original.components.find(item => item.id === id);
    assert.equal(component.status, "mapped");
    assert.equal(component.artifact, null);
    assert.throws(() => selectComponents(original, [id]), /mapped, not implemented/);
  }
  assert.ok(original.components.find(item => item.id === "tempo-cli").dependencies.includes("integration"));
});
test("T02 discovers a newly tracked unowned browser source", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-composition-map-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  assert.equal(spawnSync("git", ["init", "-q", root]).status, 0);
  mkdirSync(join(root, "app"));
  writeFileSync(join(root, "app", "new-view.tsx"), "export default null;\n");
  assert.equal(spawnSync("git", ["-C", root, "add", "app/new-view.tsx"]).status, 0);
  const paths = compositionPaths(root);
  assert.deepEqual(paths, ["app/new-view.tsx"]);
  assert.throws(() => validateCompositionOwnership(original, paths), /found 0/);
});
test("T02 mapped composition rejects unowned, duplicate and untracked files", () => {
  const paths = compositionPaths(repository);
  assert.throws(() => validateCompositionOwnership(original, [...paths, "app/new-tracked-view.tsx"]), /found 0/);
  const duplicate = structuredClone(original);
  duplicate.components.find(item => item.id === "integration").ownedFiles.push("app/page.tsx");
  assert.throws(() => validateCompositionOwnership(duplicate, paths), /found 2/);
  const stale = structuredClone(original);
  stale.components.find(item => item.id === "study-web").ownedFiles.push("app/not-tracked.tsx");
  assert.throws(() => validateCompositionOwnership(stale, paths), /untracked source/);
});
for (const [scope, expected] of [["retained", 37], ["review", 15], ["durable", 5], ["calendar", 2], ["prefix_split", 8], ["readiness", 3], ["settings", 4], ["teaching", 2], ["annotation", 2], ["activity", 2], ["integrity", 8], ["opening_graph", 4], ["pgn_import", 6], ["card_deletion", 3], ["canonical_prefix", 3], ["repertoire_changes", 4], ["card_branch_edits", 5], ["repertoire_controls", 3], ["tactics", 9], ["queue_commands", 4], ["queue_order", 7], ["queue_application", 7], ["queue_refresh", 8], ["study_attempt", 6], ["study_grading", 2], ["repertoire_conflicts", 4], ["frozen_order", 5], ["queue_attempt_origin", 3], ["opening_evidence", 6], ["position_first_response", 3], ["position_grade_effect", 3], ["position_plan", 6], ["position_source", 5], ["captured_tactic", 4], ["chess_helpers", 5], ["scheduling_review", 7], ["repertoire_snapshot", 6], ["background_activity", 3], ["background_metrics", 4], ["redis_runtime", 9], ["study_control", 3], ["repertoire_move_verification", 3], ["analysis_paste", 3], ["http_composition", 8], ["position_issuance_storage", 5], ["database_infrastructure", 4], ["opening_progression", 1], ["puzzle_helpers", 1], ["lifecycle_projection", 1], ["opening_kernels", 2], ["opening_admission", 3], ["startup_composition", 3]]) {
  test(`T02 ${scope} census derives ownership and entrypoints from registry`, () => {
    const paths = censusPaths(scope, repository);
    assert.equal(paths.length, expected);
    validateCensusOwnership(original, scope, paths);
    validateCensusInterfaces(original, scope, repository, python);
    for (const owner of original.components.filter(item => item.censusScope === scope))
      assert.throws(() => selectComponents(original, [owner.id]), /mapped, not implemented/);
  });
  test(`T02 ${scope} census rejects unowned, overlap, stale and false interface`, () => {
    const paths = censusPaths(scope, repository);
    const unowned = structuredClone(original);
    const owner = unowned.components.find(item => item.ownedFiles?.includes(paths[0]));
    owner.ownedFiles = owner.ownedFiles.filter(path => path !== paths[0]);
    assert.throws(() => validateCensusOwnership(unowned, scope, paths), /found 0/);
    const overlap = structuredClone(original);
    overlap.components.find(item => item.status === "mapped" && item.id !== owner.id).ownedFiles.push(paths[0]);
    assert.throws(() => validateCensusOwnership(overlap, scope, paths), /found 2/);
    const stale = structuredClone(original);
    stale.components.find(item => item.id === owner.id).ownedFiles.push("backend/app/untracked-task.py");
    assert.throws(() => validateCensusOwnership(stale, scope, paths), /untracked source/);
    const empty = structuredClone(original);
    empty.components.find(item => item.censusScope === scope).publicExports = [];
    assert.throws(() => validateCensusInterfaces(empty, scope, repository, python), /lacks named interfaces/);
    const escaping = structuredClone(original);
    const escapedOwner = escaping.components.find(item => item.censusScope === scope);
    escapedOwner.ownedFiles.push("../outside.py");
    escapedOwner.publicExports.push("../outside.py#read");
    assert.throws(() => validateCensusInterfaces(escaping, scope, repository, python), /Invalid census interface/);
    const wrong = structuredClone(original);
    const target = wrong.components.find(item => item.censusScope === scope);
    target.publicExports.push(target.ownedFiles[0] + "#missing");
    assert.throws(() => validateCensusInterfaces(wrong, scope, repository, python), /Missing census interface/);
  });
}
for (const [scope, path] of [["retained", "backend/app/contracts/new_contract.py"], ["review", "database/tempo_storage/study/review/new_storage.py"], ["durable", "database/tempo_storage/durable_tasks/new_task.py"], ["calendar", "backend/app/services/study_calendar_extra.py"], ["prefix_split", "backend/app/prefix_split_new.py"], ["readiness", "backend/app/operations/new_health.py"], ["settings", "database/tempo_storage/controls/settings_extra.py"], ["teaching", "backend/app/teaching_commands_extra.py"], ["annotation", "backend/app/annotation_commands_extra.py"], ["activity", "backend/app/activity_commands_extra.py"], ["integrity", "database/tempo_storage/authored_repertoires/integrity/new_adapter.py"], ["opening_graph", "database/tempo_storage/opening_graph/new_adapter.py"], ["pgn_import", "database/tempo_storage/authored_repertoires/pgn_import/new_adapter.py"], ["card_deletion", "database/tempo_storage/authored_repertoires/card_deletion/new_adapter.py"], ["canonical_prefix", "database/tempo_storage/authored_repertoires/canonical_prefix/new_adapter.py"], ["repertoire_changes", "backend/app/repertoire/changes/new_adapter.py"], ["card_branch_edits", "database/tempo_storage/authored_repertoires/card_edits/new_adapter.py"], ["repertoire_controls", "backend/app/repertoire_settings_extra.py"], ["tactics", "database/tempo_storage/tactics/new_adapter.py"], ["queue_commands", "backend/app/queue_commands_extra.py"], ["queue_order", "backend/app/services/queue_presentation_extra.py"], ["queue_application", "database/tempo_storage/queue_refresh/application/new_adapter.py"], ["queue_refresh", "backend/app/services/postgres_queue_refresh_extra.py"], ["study_attempt", "database/tempo_storage/study_attempts/new_adapter.py"], ["study_grading", "backend/app/study/policy/study_grading_extra.py"], ["repertoire_conflicts", "backend/app/studies/repertoire_conflicts_extra.py"], ["frozen_order", "backend/app/study/frozen_order/new_policy.py"], ["queue_attempt_origin", "database/tempo_storage/lifecycle/queue_attempt_origins_extra.py"], ["opening_evidence", "database/tempo_storage/opening_evidence/new_adapter.py"], ["position_first_response", "backend/app/study/position_learning/first_response_policy_extra.py"], ["position_grade_effect", "backend/app/study/position_learning/daily_credit_extra.py"], ["position_plan", "backend/app/study/position_learning/issued_plan_service_extra.py"], ["position_source", "backend/app/repertoire/position_study_source_extra.py"], ["captured_tactic", "backend/app/study/captured_tactic_extra.py"], ["chess_helpers", "backend/app/chess/new_helper.py"], ["scheduling_review", "backend/app/services/scheduling_review_application_extra.py"], ["repertoire_snapshot", "backend/app/repertoire/interfaces/new_projection.py"], ["background_activity", "database/tempo_storage/controls/activity/new_adapter.py"], ["background_metrics", "backend/app/services/background_metrics_extra.py"], ["redis_runtime", "database/tempo_storage/redis_control/new_adapter.py"], ["study_control", "backend/app/services/study_mode_extra.py"], ["repertoire_move_verification", "database/tempo_storage/repertoire_scope/new_adapter.py"], ["analysis_paste", "backend/app/services/analysis_paste_extra.py"], ["http_composition", "database/tempo_storage/http_composition/new_adapter.py"], ["position_issuance_storage", "database/tempo_storage/study/position_introductions_extra.py"], ["database_infrastructure", "database/tempo_storage/connections_extra.py"], ["opening_progression", "database/tempo_storage/opening_progression_extra.py"], ["puzzle_helpers", "backend/app/services/puzzles_extra.py"], ["lifecycle_projection", "database/tempo_storage/lifecycle/refresh_requests_extra.py"], ["opening_kernels", "backend/app/study/openings/frozen_math_extra.py"], ["opening_admission", "backend/app/services/queue_admission_extra.py"], ["startup_composition", "backend/app/tasks_extra.py"]]) {
  test(`T02 discovers newly tracked ${scope} sibling without an owner`, context => {
    const root = mkdtempSync(join(tmpdir(), "tempo-census-map-"));
    context.after(() => rmSync(root, { recursive: true, force: true }));
    assert.equal(spawnSync("git", ["init", "-q", root]).status, 0);
    mkdirSync(join(root, path, ".."), { recursive: true });
    writeFileSync(join(root, path), "pass\n");
    assert.equal(spawnSync("git", ["-C", root, "add", path]).status, 0);
    const paths = censusPaths(scope, root);
    assert.deepEqual(paths, [path]);
    assert.throws(() => validateCensusOwnership(original, scope, paths), /found 0/);
  });
}
test("T02 managed cut owners reject missing, duplicate, stale, role, export, dependency and overlap", () => {
  assert.deepEqual(validateManagedCutSources(original,repository,python),{sources:2});
  const change=(fn)=>{const bad=structuredClone(original),owner=bad.components.find(c=>c.id==="postgres-managed-isolation");fn(bad,owner);return bad;};
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.ownedFiles.pop()),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.ownedFiles.push(o.ownedFiles[0])),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.ownedFiles[0]="../foreign.py"),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.resourceRoles[0]+="-wrong"),repository,python),/managed cut role/);
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.publicExports[0]=o.ownedFiles[0]+"#missing"),repository,python),/source parse failed/);
  assert.throws(()=>validateManagedCutSources(change((_,o)=>o.dependencies=[]),repository,python),/dependency/);
  assert.throws(()=>validateManagedCutSources(change((b,o)=>b.components.find(c=>c.id==="integration").ownedFiles.push(o.ownedFiles[0])),repository,python),/overlapping/);
  const revised=change((_,o)=>{const [p]=o.resourceRoles[0].split("#");o.resourceRoles[0]=p+"#migration-state-witness";});
  assert.throws(()=>validateManagedCutSources(revised,repository,python),/managed cut role/);
});
test("T02 managed cut witnesses retain each consequential capability and owner", () => {
  for(const [id,symbol] of [["postgres-cli-state-guard","#apply_cut62_history_v2"],["postgres-managed-isolation","#execute_managed_history_cut"]]) {
    const missing=structuredClone(original),owner=missing.components.find(c=>c.id===id);
    owner.publicExports=owner.publicExports.filter(value=>!value.endsWith(symbol));
    assert.throws(()=>validateManagedCutSources(missing,repository,python),/Missing consequential managed cut capability/);
  }
  const swapped=structuredClone(original),a=swapped.components.find(c=>c.id==="postgres-cli-state-guard"),b=swapped.components.find(c=>c.id==="postgres-managed-isolation");
  [a.ownedFiles,b.ownedFiles]=[b.ownedFiles,a.ownedFiles];
  [a.publicExports,b.publicExports]=[b.publicExports,a.publicExports];
  [a.resourceRoles,b.resourceRoles]=[b.resourceRoles,a.resourceRoles];
  assert.throws(()=>validateManagedCutSources(swapped,repository,python),/managed cut role|Missing consequential/);
  a.resourceRoles=a.ownedFiles.map(path=>path+"#migration-state-witness"); b.resourceRoles=b.ownedFiles.map(path=>path+"#managed-cut-fence");
  assert.throws(()=>validateManagedCutSources(swapped,repository,python),/Missing consequential managed cut capability/);
});
test("T02 managed cut sources parse without execution and discover new siblings", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-managed-cut-source-"));context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"],{cwd:root}).status,0);
  const files=original.components.filter(c=>["postgres-cli-state-guard","postgres-managed-isolation"].includes(c.id)).flatMap(c=>c.ownedFiles);
  for(const file of files){mkdirSync(join(root,file,".."),{recursive:true});cpSync(join(repository,file),join(root,file));}
  assert.equal(spawnSync("git",["-C",root,"add","database/tooling"],{cwd:root}).status,0);
  assert.deepEqual(validateManagedCutSources(original,root,python),{sources:2});
  const target=join(root,files[0]),sentinel=join(root,"executed"),source=readFileSync(target,"utf8");
  writeFileSync(target,source+`\nfrom pathlib import Path\nPath(${JSON.stringify(sentinel)}).write_text("unsafe")\n`);
  assert.deepEqual(validateManagedCutSources(original,root,python),{sources:2});assert.equal(existsSync(sentinel),false);
  writeFileSync(target,"def invalid(:\n");assert.throws(()=>validateManagedCutSources(original,root,python),/source parse failed/);writeFileSync(target,source);
  for(const [path,name] of [["database/tooling/verify_postgres_cli_state.py","apply_cut62_history_v2"],["database/tooling/managed_isolation.py","execute_managed_history_cut"]]) {
    const file=join(root,path),before=readFileSync(file,"utf8");
    assert.ok(before.includes("def "+name+"("));writeFileSync(file,before.replace("def "+name+"(","def removed_capability("));
    assert.throws(()=>validateManagedCutSources(original,root,python),/source parse failed/);writeFileSync(file,before);
  }
  const added="database/tooling/verify_postgres_cli_state_extra.py";writeFileSync(join(root,added),"def extra(): pass\n");
  assert.equal(spawnSync("git",["-C",root,"add",added],{cwd:root}).status,0);
  assert.throws(()=>validateManagedCutSources(original,root,python),/missing, duplicate or stale/);
  const registered=structuredClone(original),owner=registered.components.find(c=>c.id==="postgres-cli-state-guard");owner.ownedFiles.push(added);owner.resourceRoles.push(added+"#migration-state-witness");owner.publicExports.push(added+"#extra");
  assert.deepEqual(validateManagedCutSources(registered,root,python),{sources:3});
  const jsx="database/tooling/verify_postgres_cli_state_extra.jsx";writeFileSync(join(root,jsx),"export default null;\n");
  assert.equal(spawnSync("git",["-C",root,"add",jsx],{cwd:root}).status,0);assert.throws(()=>validateManagedCutSources(registered,root,python),/missing, duplicate or stale/);
  owner.ownedFiles.push(jsx);owner.resourceRoles.push(jsx+"#migration-state-witness");owner.publicExports.push(jsx+"#default");
  assert.throws(()=>validateManagedCutSources(registered,root,python),/Invalid or overlapping managed cut source/);
});
test("T02 backup verification owners reject missing, duplicate, stale, role, export, dependency and overlap", () => {
  assert.deepEqual(validateBackupVerificationSources(original,repository,python),{sources:2});
  const change=(fn)=>{const bad=structuredClone(original),owner=bad.components.find(c=>c.id==="postgres-backup-verification");fn(bad,owner);return bad;};
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.ownedFiles.pop()),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.ownedFiles.push(o.ownedFiles[0])),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.ownedFiles[0]="../foreign.py"),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.resourceRoles[0]+="-wrong"),repository,python),/verification role/);
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.publicExports[0]=o.ownedFiles[0]+"#missing"),repository,python),/source parse failed/);
  assert.throws(()=>validateBackupVerificationSources(change((_,o)=>o.dependencies=[]),repository,python),/dependency/);
  assert.throws(()=>validateBackupVerificationSources(change((b,o)=>b.components.find(c=>c.id==="integration").ownedFiles.push(o.ownedFiles[0])),repository,python),/overlapping/);
  const revised=change((_,o)=>{const [p]=o.resourceRoles[0].split("#");o.resourceRoles[0]=p+"#ordered-row-fingerprint";});
  assert.deepEqual(validateBackupVerificationSources(revised,repository,python),{sources:2});
});
test("T02 backup verification sources parse without execution and discover new siblings", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-backup-source-"));context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"],{cwd:root}).status,0);
  const files=original.components.filter(c=>["postgres-fingerprint-tool","postgres-backup-verification"].includes(c.id)).flatMap(c=>c.ownedFiles);
  for(const file of files){mkdirSync(join(root,file,".."),{recursive:true});cpSync(join(repository,file),join(root,file));}
  assert.equal(spawnSync("git",["-C",root,"add","database/tooling"],{cwd:root}).status,0);
  assert.deepEqual(validateBackupVerificationSources(original,root,python),{sources:2});
  const target=join(root,files[0]),sentinel=join(root,"executed"),source=readFileSync(target,"utf8");
  writeFileSync(target,source+`\nfrom pathlib import Path\nPath(${JSON.stringify(sentinel)}).write_text("unsafe")\n`);
  assert.deepEqual(validateBackupVerificationSources(original,root,python),{sources:2});assert.equal(existsSync(sentinel),false);
  writeFileSync(target,"def invalid(:\n");assert.throws(()=>validateBackupVerificationSources(original,root,python),/source parse failed/);writeFileSync(target,source);
  const added="database/tooling/postgres_fingerprint_extra.py";writeFileSync(join(root,added),"def extra(): pass\n");
  assert.equal(spawnSync("git",["-C",root,"add",added],{cwd:root}).status,0);
  assert.throws(()=>validateBackupVerificationSources(original,root,python),/missing, duplicate or stale/);
  const registered=structuredClone(original),owner=registered.components.find(c=>c.id==="postgres-fingerprint-tool");owner.ownedFiles.push(added);owner.resourceRoles.push(added+"#ordered-row-fingerprint");owner.publicExports.push(added+"#extra");
  assert.deepEqual(validateBackupVerificationSources(registered,root,python),{sources:3});
  const jsx="database/tooling/postgres_fingerprint_extra.jsx";writeFileSync(join(root,jsx),"export default null;\n");
  assert.equal(spawnSync("git",["-C",root,"add",jsx],{cwd:root}).status,0);assert.throws(()=>validateBackupVerificationSources(registered,root,python),/missing, duplicate or stale/);
  owner.ownedFiles.push(jsx);owner.resourceRoles.push(jsx+"#ordered-row-fingerprint");owner.publicExports.push(jsx+"#default");
  assert.throws(()=>validateBackupVerificationSources(registered,root,python),/Invalid or overlapping backup verification source/);
});
test("T02 database CLI source owners reject missing, duplicate, stale, role, export and overlap", () => {
  assert.deepEqual(validateDatabaseCliTools(original,repository),{sources:2});
  const change=(fn)=>{const bad=structuredClone(original),owner=bad.components.find(c=>c.id==="database-cli-probes");fn(bad,owner);return bad;};
  assert.throws(()=>validateDatabaseCliTools(change((_,o)=>o.ownedFiles.pop()),repository),/missing, duplicate or stale/);
  assert.throws(()=>validateDatabaseCliTools(change((_,o)=>o.ownedFiles.push(o.ownedFiles[0])),repository),/missing, duplicate or stale/);
  assert.throws(()=>validateDatabaseCliTools(change((_,o)=>o.ownedFiles[0]="../foreign.mjs"),repository),/missing, duplicate or stale/);
  assert.throws(()=>validateDatabaseCliTools(change((_,o)=>o.resourceRoles[0]+="-wrong"),repository),/CLI role/);
  assert.throws(()=>validateDatabaseCliTools(change((_,o)=>o.publicExports.pop()),repository),/interface mismatch/);
  assert.throws(()=>validateDatabaseCliTools(change((b,o)=>b.components.find(c=>c.id==="integration").ownedFiles.push(o.ownedFiles[0])),repository),/overlapping/);
  const revised=change((_,o)=>{const [p]=o.resourceRoles[0].split("#");o.resourceRoles[0]=p+"#cli-backup-operations";});
  assert.deepEqual(validateDatabaseCliTools(revised,repository),{sources:2});
});
test("T02 database CLI guard parses JS without invoking operations and discovers siblings", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-db-cli-source-"));context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"],{cwd:root}).status,0);
  const files=original.components.filter(c=>["database-cli-probes","database-cli-backup-operations"].includes(c.id)).flatMap(c=>c.ownedFiles);
  for(const file of files){mkdirSync(join(root,file,".."),{recursive:true});cpSync(join(repository,file),join(root,file));}
  assert.equal(spawnSync("git",["-C",root,"add","database/tooling/cli"],{cwd:root}).status,0);
  assert.deepEqual(validateDatabaseCliTools(original,root),{sources:2});
  const target=join(root,files[0]);writeFileSync(target,"export function broken( {\n");
  assert.throws(()=>validateDatabaseCliTools(original,root),/syntax invalid/);
  writeFileSync(target,'throw new Error("must not execute");\n');
  assert.throws(()=>validateDatabaseCliTools(original,root),/top-level effect/);cpSync(join(repository,files[0]),target);
  const added="database/tooling/cli/extra.mjs";writeFileSync(join(root,added),"export function extra() {}\n");
  assert.equal(spawnSync("git",["-C",root,"add",added],{cwd:root}).status,0);
  assert.throws(()=>validateDatabaseCliTools(original,root),/missing, duplicate or stale/);
  const registered=structuredClone(original),owner=registered.components.find(c=>c.id==="database-cli-probes");
  owner.ownedFiles.push(added);owner.resourceRoles.push(added+"#cli-diagnostic-probes");owner.publicExports.push(added+"#extra");
  assert.deepEqual(validateDatabaseCliTools(registered,root),{sources:3});
  const jsx="database/tooling/cli/extra.jsx";writeFileSync(join(root,jsx),"export default null;\n");
  assert.equal(spawnSync("git",["-C",root,"add",jsx],{cwd:root}).status,0);
  assert.throws(()=>validateDatabaseCliTools(registered,root),/missing, duplicate or stale/);
  owner.ownedFiles.push(jsx);owner.resourceRoles.push(jsx+"#cli-diagnostic-probes");owner.publicExports.push(jsx+"#default");
  assert.throws(()=>validateDatabaseCliTools(registered,root),/Invalid or overlapping database CLI source/);
});
test("T02 historical reduction tools reject missing, duplicate, stale, role and overlap", () => {
  assert.deepEqual(validateReductionTools(original,repository,python),{files:3});
  const change=(fn)=>{const bad=structuredClone(original),owner=bad.components.find(c=>c.id==="historical-reduction-audit-tooling");fn(bad,owner);return bad;};
  assert.throws(()=>validateReductionTools(change((_,o)=>o.ownedFiles.pop()),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateReductionTools(change((_,o)=>o.ownedFiles.push(o.ownedFiles[0])),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateReductionTools(change((_,o)=>o.ownedFiles[0]="../foreign.py"),repository,python),/missing, duplicate or stale/);
  assert.throws(()=>validateReductionTools(change((_,o)=>o.resourceRoles[0]+="-wrong"),repository,python),/tooling role/);
  const revised=change((_,o)=>{const [path]=o.resourceRoles[0].split("#");o.resourceRoles[0]=path+"#source-link-audit";});
  assert.deepEqual(validateReductionTools(revised,repository,python),{files:3});
  assert.throws(()=>validateReductionTools(change((b,o)=>b.components.find(c=>c.id==="integration").ownedFiles.push(o.ownedFiles[0])),repository,python),/overlapping/);
});
test("T02 historical reduction tool guard discovers new files and parses without execution", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-reduction-source-"));context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"],{cwd:root}).status,0);
  const owner=original.components.find(c=>c.id==="historical-reduction-audit-tooling");
  for(const file of owner.ownedFiles){mkdirSync(join(root,file,".."),{recursive:true});cpSync(join(repository,file),join(root,file));}
  assert.equal(spawnSync("git",["-C",root,"add","tools/reduction"],{cwd:root}).status,0);
  assert.deepEqual(validateReductionTools(original,root,python),{files:3});
  const target=join(root,owner.ownedFiles[0]), sentinel=join(root,"executed");
  writeFileSync(target,`from pathlib import Path\nPath(${JSON.stringify(sentinel)}).write_text("unsafe")\n`);
  assert.deepEqual(validateReductionTools(original,root,python),{files:3}); assert.equal(existsSync(sentinel),false);
  writeFileSync(target,"def invalid(:\n"); assert.throws(()=>validateReductionTools(original,root,python),/source parse failed/);
  cpSync(join(repository,owner.ownedFiles[0]),target);
  const added="tools/reduction/new_audit.py";writeFileSync(join(root,added),"");
  assert.equal(spawnSync("git",["-C",root,"add",added],{cwd:root}).status,0);
  assert.throws(()=>validateReductionTools(original,root,python),/missing, duplicate or stale/);
  const registered=structuredClone(original), newOwner=registered.components.find(c=>c.id==="historical-reduction-audit-tooling");
  newOwner.ownedFiles.push(added);newOwner.resourceRoles.push(added+"#source-census");
  assert.deepEqual(validateReductionTools(registered,root,python),{files:4});
  newOwner.resourceRoles[newOwner.resourceRoles.length-1]=added+"#unsupported";
  assert.throws(()=>validateReductionTools(registered,root,python),/tooling role/);
  newOwner.resourceRoles[newOwner.resourceRoles.length-1]=added+"#source-census";
  const jsx="tools/reduction/new_view.jsx"; writeFileSync(join(root,jsx),"export default null;\n");
  assert.equal(spawnSync("git",["-C",root,"add",jsx],{cwd:root}).status,0);
  assert.throws(()=>validateReductionTools(registered,root,python),/missing, duplicate or stale/);
  newOwner.ownedFiles.push(jsx);newOwner.resourceRoles.push(jsx+"#source-census");
  assert.throws(()=>validateReductionTools(registered,root,python),/tooling role/);
});
test("T02 contract tooling roles reject missing, duplicate, stale, wrong role and overlap", () => {
  assert.deepEqual(validateContractTooling(original, repository, python), {sources:12});
  const missing=structuredClone(original); missing.components.find(c=>c.id==="contract-exchange-tooling").ownedFiles.pop();
  assert.throws(()=>validateContractTooling(missing,repository,python),/missing, duplicate or stale/);
  const duplicate=structuredClone(original), d=duplicate.components.find(c=>c.id==="contract-exchange-tooling");d.ownedFiles.push(d.ownedFiles[0]);
  assert.throws(()=>validateContractTooling(duplicate,repository,python),/missing, duplicate or stale/);
  const stale=structuredClone(original); stale.components.find(c=>c.id==="contract-exchange-tooling").ownedFiles[0]="contracts/not-tracked.py";
  assert.throws(()=>validateContractTooling(stale,repository,python),/missing, duplicate or stale/);
  const wrong=structuredClone(original); wrong.components.find(c=>c.id==="contract-exchange-tooling").resourceRoles[0]+="-wrong";
  assert.throws(()=>validateContractTooling(wrong,repository,python),/role/);
  const reclassified=structuredClone(original), changed=reclassified.components.find(c=>c.id==="contract-exchange-tooling");
  changed.resourceRoles[0]=changed.resourceRoles[0].replace("#boundary-check", "#suite-runner");
  assert.deepEqual(validateContractTooling(reclassified,repository,python),{sources:12});
  const duplicateRole=structuredClone(original), dr=duplicateRole.components.find(c=>c.id==="contract-exchange-tooling"); dr.resourceRoles[1]=dr.resourceRoles[0];
  assert.throws(()=>validateContractTooling(duplicateRole,repository,python),/role/);
  const overlap=structuredClone(original);overlap.components.find(c=>c.id==="integration").ownedFiles.push("contracts/run_tests.py");
  assert.throws(()=>validateContractTooling(overlap,repository,python),/overlapping/);
});
test("T02 contract tooling discovers new tracked sources and rejects malformed source without execution", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-contract-source-")); context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"]).status,0);
  const files=original.components.filter(c=>["contract-exchange-tooling","contract-exchange-tests","contract-typescript-consumer"].includes(c.id)).flatMap(c=>c.ownedFiles);
  for(const path of files){mkdirSync(join(root,path,".."),{recursive:true});cpSync(join(repository,path),join(root,path));}
  assert.equal(spawnSync("git",["-C",root,"add","contracts"]).status,0);
  assert.deepEqual(validateContractTooling(original,root,python),{sources:12});
  const bad=join(root,"contracts/run_tests.py"),bytes=readFileSync(bad,"utf8"); writeFileSync(bad,"def broken(\n");
  assert.throws(()=>validateContractTooling(original,root,python),/syntax/);writeFileSync(bad,bytes);
  const badTs=join(root,"contracts/consumer-examples/src/semantics.ts"),tsBytes=readFileSync(badTs,"utf8");writeFileSync(badTs,"export function broken(\n");
  assert.throws(()=>validateContractTooling(original,root,python),/syntax/);writeFileSync(badTs,tsBytes);
  const added=join(root,"contracts/new_tool.py");writeFileSync(added,"raise RuntimeError('must not execute')\n");
  assert.equal(spawnSync("git",["-C",root,"add","contracts/new_tool.py"]).status,0);
  assert.throws(()=>validateContractTooling(original,root,python),/missing, duplicate or stale/);
  const extended=structuredClone(original), owner=extended.components.find(c=>c.id==="contract-exchange-tooling");
  owner.ownedFiles.push("contracts/new_tool.py"); owner.resourceRoles.push("contracts/new_tool.py#suite-runner");
  assert.deepEqual(validateContractTooling(extended,root,python),{sources:13});
});
test("T02 DDL source version rejects empty, missing, duplicate and mismatched history", () => {
  const files = Array.from({length:66}, (_,i) => String(i+1).padStart(3,"0") + "_example.sql");
  const schema = "POSTGRES_SCHEMA_VERSION = 66\n";
  assert.equal(validateMigrationInventory(files,schema),66);
  assert.throws(()=>validateMigrationInventory([],schema),/unique and contiguous/);
  assert.throws(()=>validateMigrationInventory(files.filter(f=>!f.startsWith("032_")),schema),/unique and contiguous/);
  assert.throws(()=>validateMigrationInventory([...files,"032_duplicate.sql"],schema),/unique and contiguous/);
  assert.throws(()=>validateMigrationInventory(files,"POSTGRES_SCHEMA_VERSION = 65\n"),/disagrees/);
});
test("T02 DDL resource census rejects missing, duplicate, stale and wrong roles", () => {
  assert.deepEqual(validateDdlResources(original, repository, python), {resources:73, version:66});
  const missing=structuredClone(original), owner=missing.components.find(c=>c.id==="postgres-schema-source"); owner.ownedFiles.pop();
  assert.throws(()=>validateDdlResources(missing,repository,python), /missing, duplicate or stale/);
  const duplicate=structuredClone(original), d=duplicate.components.find(c=>c.id==="postgres-schema-source"); d.ownedFiles.push(d.ownedFiles[0]);
  assert.throws(()=>validateDdlResources(duplicate,repository,python), /missing, duplicate or stale/);
  const stale=structuredClone(original), st=stale.components.find(c=>c.id==="postgres-schema-source"); st.ownedFiles[0]="../foreign.sql";
  assert.throws(()=>validateDdlResources(stale,repository,python), /missing, duplicate or stale/);
  const wrong=structuredClone(original), w=wrong.components.find(c=>c.id==="postgres-schema-source"); w.resourceRoles[0]+="-wrong";
  assert.throws(()=>validateDdlResources(wrong,repository,python), /resource role/);
  const overlap=structuredClone(original); overlap.components.find(c=>c.id==="integration").ownedFiles.push(overlap.components.find(c=>c.id==="postgres-schema-source").ownedFiles[0]);
  assert.throws(()=>validateDdlResources(overlap,repository,python), /overlapping DDL/);
});
test("T02 DDL resource guard detects changed history and newly tracked SQL", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-ddl-source-")); context.after(()=>rmSync(root,{recursive:true,force:true}));
  assert.equal(spawnSync("git",["-C",root,"init","-q"],{cwd:root}).status,0);
  const files=original.components.filter(c=>["postgres-schema-source","postgres-migration-tooling"].includes(c.id)).flatMap(c=>c.ownedFiles);
  for(const path of files){mkdirSync(join(root,path,".."),{recursive:true});cpSync(join(repository,path),join(root,path));}
  assert.equal(spawnSync("git",["-C",root,"add","database"],{cwd:root}).status,0);
  assert.deepEqual(validateDdlResources(original,root,python),{resources:73,version:66});
  const history="database/tempo_storage/ddl/historical/050_remediation.sql"; writeFileSync(join(root,history),"changed");
  assert.throws(()=>validateDdlResources(original,root,python),/Historical migration resource mismatch/); cpSync(join(repository,history),join(root,history));
  const tool="database/tooling/apply_postgres_migrations.py", target=join(root,tool), source=readFileSync(target,"utf8");
  writeFileSync(target,source.replace('"050_learning_remediation.sql": (','"051_foreign.sql": ('));
  assert.throws(()=>validateDdlResources(original,root,python),/DDL source witness failed/); writeFileSync(target,source);
  const added="database/tempo_storage/ddl/migrations/067_unlisted.sql"; writeFileSync(join(root,added),"");
  assert.equal(spawnSync("git",["-C",root,"add",added],{cwd:root}).status,0);
  assert.throws(()=>validateDdlResources(original,root,python),/missing, duplicate or stale/);
});
test("T02 startup census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "startup_composition") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "startup_composition", censusPaths("startup_composition", repository)), /lacks owners/);
});
test("T02 startup witness rejects foreign loader under optimized Python", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-startup-optimize-")); context.after(() => rmSync(root, {recursive:true, force:true}));
  mkdirSync(join(root, "backend/app"), {recursive:true});
  for (const name of ["main.py", "tasks.py", "celery_app.py"]) {
    let source = readFileSync(join(repository, "backend/app", name), "utf8");
    if (name === "celery_app.py") source = source.replace('include=["app.tasks"]', 'include=["app.foreign"]');
    writeFileSync(join(root, "backend/app", name), source);
  }
  const old = process.env.PYTHONOPTIMIZE; process.env.PYTHONOPTIMIZE = "1";
  try { assert.throws(() => validateStartupFacts(original, root, python), /Startup source witness failed/); }
  finally { if (old === undefined) delete process.env.PYTHONOPTIMIZE; else process.env.PYTHONOPTIMIZE = old; }
});
test("T02 startup import graph rejects a missing lazy import edge", () => {
  const bad = structuredClone(original), owner = bad.components.find(c => c.id === "queue-refresh-wakeup");
  owner.dependencies = owner.dependencies.filter(id => id !== "celery-startup-composition");
  assert.throws(() => validateStartupFacts(bad, repository, python), /Missing startup import dependency/);
});
test("T02 startup import graph rejects an actual dependency cycle", () => {
  const bad = structuredClone(original);
  bad.components.find(c => c.id === "celery-startup-composition").dependencies.push("worker-startup-composition");
  assert.throws(() => validateStartupFacts(bad, repository, python), /dependency cycle/);
});
test("T02 startup witnesses reject missing and foreign loader assignments", () => {
  validateStartupFacts(original, repository, python);
  for (const [field, value] of [["moduleAssignments", []], ["moduleAssignments", ["../outside.py#celery_app"]], ["lifecycleEdges", []], ["lifecycleEdges", ["backend/app/celery_app.py#celery-loader#unknown#app.tasks"]], ["lifecycleEdges", ["backend/app/celery_app.py#celery-loader#worker-startup-composition#app.tasks", "backend/app/celery_app.py#celery-loader#worker-startup-composition#app.tasks"]]]) {
    const bad = structuredClone(original); bad.components.find(c => c.id === "celery-startup-composition")[field] = value;
    assert.throws(() => validateStartupFacts(bad, repository, python), /Invalid startup/);
  }
});
for (const [label, file, before, after] of [
  ["assignment", "celery_app.py", "celery_app = CoalescingCelery(", "foreign_app = CoalescingCelery("],
  ["loader", "celery_app.py", 'include=["app.tasks"]', 'include=["app.foreign"]'],
  ["task route", "celery_app.py", '"app.tasks.recover_study_operations": {"queue": "foreground"}', '"app.tasks.foreign": {"queue": "foreground"}'],
  ["task queue", "celery_app.py", '"app.tasks.recover_study_operations": {"queue": "foreground"}', '"app.tasks.recover_study_operations": {"queue": "background"}'],
  ["beat target", "celery_app.py", '"task": "app.tasks.recover_study_operations"', '"task": "app.tasks.foreign"'],
  ["task registration", "tasks.py", 'name="app.tasks.recover_study_operations"', 'name="app.tasks.foreign"'],
  ["registrar", "tasks.py", 'from . import study_attempt_commands', 'from . import foreign_commands'],
  ["checkpoint", "tasks.py", 'register_command("opening_evidence.checkpoint"', 'register_command("foreign.checkpoint"'],
  ["router", "main.py", 'app.include_router(study_exercise_router)', 'app.include_router(foreign_router)'],
]) test(`T02 startup witness rejects changed ${label}`, context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-startup-witness-")); context.after(() => rmSync(root, {recursive:true, force:true}));
  mkdirSync(join(root, "backend/app"), {recursive:true});
  for (const name of ["main.py", "tasks.py", "celery_app.py"]) {
    let source = readFileSync(join(repository, "backend/app", name), "utf8");
    if (name === file) { assert.ok(source.includes(before)); source = source.replace(before, after); }
    writeFileSync(join(root, "backend/app", name), source);
  }
  assert.throws(() => validateStartupFacts(original, root, python), /Startup source witness failed/);
});
test("T02 opening admission census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "opening_admission") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "opening_admission", censusPaths("opening_admission", repository)), /lacks owners/);
});
test("T02 opening kernels census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "opening_kernels") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "opening_kernels", censusPaths("opening_kernels", repository)), /lacks owners/);
});
test("T02 lifecycle projection census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "lifecycle_projection") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "lifecycle_projection", censusPaths("lifecycle_projection", repository)), /lacks owners/);
});
test("T02 puzzle helpers census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "puzzle_helpers") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "puzzle_helpers", censusPaths("puzzle_helpers", repository)), /lacks owners/);
});
test("T02 opening progression census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "opening_progression") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "opening_progression", censusPaths("opening_progression", repository)), /lacks owners/);
});
test("T02 database infrastructure census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "database_infrastructure") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "database_infrastructure", censusPaths("database_infrastructure", repository)), /lacks owners/);
});
test("T02 position issuance storage census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "position_issuance_storage") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "position_issuance_storage", censusPaths("position_issuance_storage", repository)), /lacks owners/);
});
test("T02 HTTP composition census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "http_composition") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "http_composition", censusPaths("http_composition", repository)), /lacks owners/);
});
test("T02 analysis paste census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "analysis_paste") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "analysis_paste", censusPaths("analysis_paste", repository)), /lacks owners/);
});
test("T02 repertoire move verification census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "repertoire_move_verification") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "repertoire_move_verification", censusPaths("repertoire_move_verification", repository)), /lacks owners/);
});
test("T02 Study control census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "study_control") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "study_control", censusPaths("study_control", repository)), /lacks owners/);
});
test("T02 Redis runtime census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "redis_runtime") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "redis_runtime", censusPaths("redis_runtime", repository)), /lacks owners/);
});
test("T02 background metrics census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "background_metrics") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "background_metrics", censusPaths("background_metrics", repository)), /lacks owners/);
});
test("T02 background activity census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "background_activity") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "background_activity", censusPaths("background_activity", repository)), /lacks owners/);
});
test("T02 repertoire snapshot census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "repertoire_snapshot") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "repertoire_snapshot", censusPaths("repertoire_snapshot", repository)), /lacks owners/);
});
test("T02 scheduling review census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "scheduling_review") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "scheduling_review", censusPaths("scheduling_review", repository)), /lacks owners/);
});
test("T02 chess helpers census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "chess_helpers") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "chess_helpers", censusPaths("chess_helpers", repository)), /lacks owners/);
});
test("T02 captured tactic census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "captured_tactic") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "captured_tactic", censusPaths("captured_tactic", repository)), /lacks owners/);
});
test("T02 position source census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "position_source") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "position_source", censusPaths("position_source", repository)), /lacks owners/);
});
test("T02 position plan census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "position_plan") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "position_plan", censusPaths("position_plan", repository)), /lacks owners/);
});
test("T02 position grade effect census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "position_grade_effect") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "position_grade_effect", censusPaths("position_grade_effect", repository)), /lacks owners/);
});
test("T02 position first response census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "position_first_response") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "position_first_response", censusPaths("position_first_response", repository)), /lacks owners/);
});
test("T02 opening evidence census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "opening_evidence") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "opening_evidence", censusPaths("opening_evidence", repository)), /lacks owners/);
});
test("T02 queue attempt origin census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "queue_attempt_origin") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "queue_attempt_origin", censusPaths("queue_attempt_origin", repository)), /lacks owners/);
});
test("T02 frozen order census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "frozen_order") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "frozen_order", censusPaths("frozen_order", repository)), /lacks owners/);
});
test("T02 repertoire conflicts census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "repertoire_conflicts") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "repertoire_conflicts", censusPaths("repertoire_conflicts", repository)), /lacks owners/);
});
test("T02 Study grading census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "study_grading") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "study_grading", censusPaths("study_grading", repository)), /lacks owners/);
});
test("T02 Study attempt census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "study_attempt") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "study_attempt", censusPaths("study_attempt", repository)), /lacks owners/);
});
test("T02 queue refresh census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "queue_refresh") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "queue_refresh", censusPaths("queue_refresh", repository)), /lacks owners/);
});
test("T02 queue application census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "queue_application") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "queue_application", censusPaths("queue_application", repository)), /lacks owners/);
});
test("T02 queue order census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "queue_order") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "queue_order", censusPaths("queue_order", repository)), /lacks owners/);
});
test("T02 queue commands census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "queue_commands") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "queue_commands", censusPaths("queue_commands", repository)), /lacks owners/);
});
test("T02 tactics census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "tactics") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "tactics", censusPaths("tactics", repository)), /lacks owners/);
});
test("T02 repertoire controls census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "repertoire_controls") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "repertoire_controls", censusPaths("repertoire_controls", repository)), /lacks owners/);
});
test("T02 card and branch edits census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "card_branch_edits") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "card_branch_edits", censusPaths("card_branch_edits", repository)), /lacks owners/);
});
test("T02 repertoire changes census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "repertoire_changes") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "repertoire_changes", censusPaths("repertoire_changes", repository)), /lacks owners/);
});
test("T02 canonical prefix census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "canonical_prefix") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "canonical_prefix", censusPaths("canonical_prefix", repository)), /lacks owners/);
});
test("T02 card deletion census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "card_deletion") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "card_deletion", censusPaths("card_deletion", repository)), /lacks owners/);
});
test("T02 PGN import census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "pgn_import") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "pgn_import", censusPaths("pgn_import", repository)), /lacks owners/);
});
test("T02 opening graph census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "opening_graph") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "opening_graph", censusPaths("opening_graph", repository)), /lacks owners/);
});
test("T02 integrity census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "integrity") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "integrity", censusPaths("integrity", repository)), /lacks owners/);
});
test("T02 activity census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "activity") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "activity", censusPaths("activity", repository)), /lacks owners/);
});
test("T02 annotation census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "annotation") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "annotation", censusPaths("annotation", repository)), /lacks owners/);
});
test("T02 teaching census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "teaching") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "teaching", censusPaths("teaching", repository)), /lacks owners/);
});
test("T02 settings census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "settings") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "settings", censusPaths("settings", repository)), /lacks owners/);
});
test("T02 readiness census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "readiness") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "readiness", censusPaths("readiness", repository)), /lacks owners/);
});
test("T02 prefix split census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "prefix_split") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "prefix_split", censusPaths("prefix_split", repository)), /lacks owners/);
});
test("T02 calendar census cannot disappear when all owner tags are removed", () => {
  const missing = structuredClone(original);
  for (const component of missing.components) if (component.censusScope === "calendar") delete component.censusScope;
  assert.throws(() => validateCensusOwnership(missing, "calendar", censusPaths("calendar", repository)), /lacks owners/);
});
test("Learning review registers its actual application, tooling and native suites", () => {
  const componentIds = ["learning-review-application"];
  validateManifest(original, repository, { componentIds });
  for (const id of ["browser-intents", "study-session"]) {
    assert.equal(original.components.find(component => component.id === id)?.status, "implemented");
  }
  const environment = { ...process.env, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"),
    "--component", "learning-review-application", "--list"], { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const { selected } = JSON.parse(result.stdout);
  const nativeOwner = original.components.find(component => component.id === "learning-review-application").tests;
  assert.deepEqual(selected.filter(suite => suite.owner === "learning-review-application").map(({id, ...suite}) => suite), nativeOwner);
  assert.equal(selected.filter(suite => suite.owner === "learning-review-application").length, 3);
  assert.ok(selected.some(suite => suite.owner === "learning-review-application" && suite.kind === "postgres"));
});
test("#200 registered pure packages validate and list their actual owning suites without setup", () => {
  const componentIds = ["scheduling", "chess-core"];
  const checked = checkComponents(repository, python, { componentIds });
  assert.deepEqual(checked.map(component => component.id), componentIds);
  const environment = { ...process.env, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"),
    "--component", "scheduling", "--component", "chess-core", "--list"],
  { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const { selected } = JSON.parse(result.stdout);
  assert.deepEqual(selected.map(suite => [suite.id, suite.owner, suite.cwd]),
    componentIds.map(id => [id, id, `packages/${id}`]));
});

function contractFixture(context) {
  const root = mkdtempSync(join(tmpdir(), "tempo-d-contract-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "contracts"));
  for (const name of ["catalog.json", "check_dependencies.py"]) cpSync(join(repository, "contracts", name), join(root, "contracts", name));
  cpSync(join(repository, "contracts/game-evidence"), join(root, "contracts/game-evidence"), { recursive: true });
  const component = structuredClone(original.components.find(value => value.id === "game-evidence-contracts"));
  Object.assign(component, {
    status: "implemented", catalog: "contracts/catalog.json", testInventory: "REGRESSIONS.md",
    buildInputs: ["pyproject.toml", "src", "schemas", "fixtures", "examples", "tests", "README.md", "REGRESSIONS.md"],
    buildCommand: "python -m build --no-isolation",
    tests: [{ owner: "game-evidence-contracts", kind: "python", cwd: "contracts/game-evidence", command: "python -m pytest tests", inputs: ["tests"] }],
  });
  const manifest = { schemaVersion: 1, components: [component] };
  const save = () => writeFileSync(join(root, "components.json"), JSON.stringify(manifest));
  save();
  return { root, manifest, component, save };
}
function check(fixture, options = {}) {
  fixture.save();
  return checkComponents(fixture.root, python, { componentIds: ["game-evidence-contracts"], ...options });
}
test("D contract registration validates versioned namespaces without application source", context => {
  const fixture = contractFixture(context);
  assert.equal(check(fixture)[0].id, "game-evidence-contracts");
});
test("D implemented contract rejects missing source", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/src"), { recursive: true });
  assert.throws(() => check(fixture), /input|source/i);
});
for (const field of ["name", "version"]) test(`D contract rejects wrong artifact ${field}`, context => {
  const fixture = contractFixture(context);
  fixture.component.artifact[field] = "wrong";
  assert.throws(() => check(fixture), /identity|artifact/i);
});
test("D contract rejects missing public namespace", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/src/tempo_game_evidence_contracts/v1.py"));
  assert.throws(() => check(fixture), /namespace|module/i);
});
test("D contract rejects a missing schema rather than accepting an empty glob", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/schemas/v1/ImportedGame.schema.json"));
  assert.throws(() => check(fixture), /schema/i);
});
test("D contract cannot declare schemas omitted from its wheel", context => {
  const fixture = contractFixture(context);
  const project = join(fixture.root, "contracts/game-evidence/pyproject.toml");
  writeFileSync(project, readFileSync(project, "utf8").replace('"schemas" = "tempo_game_evidence_contracts/schemas"', '"fixtures" = "tempo_game_evidence_contracts/fixtures"'));
  assert.throws(() => check(fixture), /schema|export/i);
});
test("D contract reuses the owning checker for undeclared lazy imports", context => {
  const fixture = contractFixture(context);
  writeFileSync(join(fixture.root, "contracts/game-evidence/src/tempo_game_evidence_contracts/hidden.py"), "def deferred():\n    import services.content.internal\n");
  assert.throws(() => check(fixture), /undeclared|cross-component/i);
});
test("D selectors reject unknown, empty and planned owners", context => {
  const fixture = contractFixture(context);
  assert.throws(() => check(fixture, { componentIds: ["unknown"] }), /Unknown component/);
  assert.throws(() => check(fixture, { componentIds: [] }), /[Ee]mpty|[Zz]ero/);
  fixture.component.status = "planned";
  fixture.component.buildCommand = null;
  fixture.component.tests = [];
  assert.throws(() => check(fixture), /planned|implemented/i);
});
test("D implemented suite rejects an incorrect cwd", context => {
  const fixture = contractFixture(context);
  fixture.component.tests[0].cwd = "absent";
  assert.throws(() => check(fixture), /cwd|working directory/i);
});
test("D planned component cannot claim executable tests", context => {
  const fixture = contractFixture(context);
  fixture.component.status = "planned";
  fixture.component.buildCommand = null;
  assert.throws(() => validateManifest(fixture.manifest, fixture.root), /Planned component/);
});
test("D selected TypeScript validation never invokes Python or needs backend source", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-d-typescript-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const component = original.components.find(value => value.id === "chess-line");
  mkdirSync(join(root, "packages"));
  cpSync(join(repository, component.root), join(root, component.root), { recursive: true });
  const dependencyRoot = existsSync(join(repository, "node_modules/chess.js")) ? "node_modules" : "packages/chess-line/node_modules";
  symlinkSync(join(repository, dependencyRoot), join(root, "node_modules"));
  writeFileSync(join(root, "components.json"), JSON.stringify({ schemaVersion: 1, components: [component] }));
  assert.equal(checkComponents(root, "/python-must-not-run", { componentIds: ["chess-line"] })[0].id, "chess-line");
});
test("T02 copied checker closure selects and lists without the DDL module", context => {
  const root=mkdtempSync(join(tmpdir(),"tempo-checker-closure-")); context.after(()=>rmSync(root,{recursive:true,force:true}));
  const component=original.components.find(c=>c.id==="chess-line");
  mkdirSync(join(root,"tools/component-build"),{recursive:true}); mkdirSync(join(root,"packages"),{recursive:true});
  for(const name of ["check-components.mjs","selection.mjs","worker-qualifier.mjs"]) cpSync(join(repository,"tools/component-build",name),join(root,"tools/component-build",name));
  cpSync(join(repository,"components.schema.json"),join(root,"components.schema.json"));
  cpSync(join(repository,component.root),join(root,component.root),{recursive:true});
  symlinkSync(join(repository,"node_modules"),join(root,"node_modules"));
  writeFileSync(join(root,"components.json"),JSON.stringify({schemaVersion:1,components:[component]}));
  assert.equal(existsSync(join(root,"database")),false);
  const cli=join(root,"tools/component-build/check-components.mjs"),env={...process.env,TEMPO_COMPONENT_PYTHON:"/python-must-not-run"}; delete env.TEMPO_COMPONENT_ROOT;
  for(const args of [["--component","chess-line"],["--component","chess-line","--list"]]) {
    const result=spawnSync(process.execPath,[cli,...args],{cwd:root,env,encoding:"utf8"});
    assert.equal(result.status,0,result.stdout+result.stderr);
  }
});
test("T02 DDL census does not run for selected or minimal component checks and listings", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-ddl-minimal-"));
  context.after(() => rmSync(root, {recursive:true, force:true}));
  const component = original.components.find(c => c.id === "chess-line");
  mkdirSync(join(root,"packages")); cpSync(join(repository,component.root),join(root,component.root),{recursive:true});
  const dependencyRoot = existsSync(join(repository,"node_modules/chess.js")) ? "node_modules" : "packages/chess-line/node_modules";
  symlinkSync(join(repository,dependencyRoot),join(root,"node_modules"));
  writeFileSync(join(root,"components.json"),JSON.stringify({schemaVersion:1,components:[component]}));
  assert.equal(existsSync(join(root,"database")),false);
  assert.equal(checkComponents(root,"/python-must-not-run",{componentIds:["chess-line"]})[0].id,"chess-line");
  assert.equal(checkComponents(repository,"/python-must-not-run",{componentIds:["chess-line"]})[0].id,"chess-line");
  const cli=join(repository,"tools/component-build/check-components.mjs"), env={...process.env,TEMPO_COMPONENT_ROOT:root,TEMPO_COMPONENT_PYTHON:"/python-must-not-run"};
  for (const args of [["--list"],["--component","chess-line","--list"]]) {
    const result=spawnSync(process.execPath,[cli,...args],{cwd:root,env,encoding:"utf8"});
    assert.equal(result.status,0,result.stdout+result.stderr); assert.equal(JSON.parse(result.stdout).selected[0].id,"chess-line");
  }
  const full=spawnSync(process.execPath,[cli,"--list"],{cwd:repository,env:{...process.env,TEMPO_COMPONENT_PYTHON:"/python-must-not-run"},encoding:"utf8"});
  assert.notEqual(full.status,0); assert.match(full.stderr,/DDL source witness failed|python-must-not-run/);
});
test("D component listing performs no build, producer or test execution", context => {
  const fixture = contractFixture(context);
  const runner = resolve(repository, "tools/component-build/test-lane.mjs");
  const environment = { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [runner, "--component", "game-evidence-contracts", "--list"], { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /game-evidence-contracts/);
  assert.equal(existsSync(join(fixture.root, "contracts/game-evidence/dist")), false);
});

function invokeLane(fixture, argumentsList, environment = {}) {
  fixture.save();
  const env = { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: python, ...environment };
  delete env.NODE_TEST_CONTEXT;
  return spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"), ...argumentsList], { env, encoding: "utf8", timeout: 15_000 });
}
function nodeSuite(fixture) {
  const tests = join(fixture.root, "contracts/game-evidence/tests");
  writeFileSync(join(tests, "dispatch.test.mjs"), `import {test} from "node:test"; import assert from "node:assert/strict"; import {appendFileSync} from "node:fs";
test("actual selected contract fixture",()=>{ assert.equal(process.cwd(), ${JSON.stringify(realpathSync(join(fixture.root, "contracts/game-evidence")))}); appendFileSync("executions", "case\\n"); });`);
  return { owner: "game-evidence-contracts", kind: "node", cwd: "contracts/game-evidence", inputs: ["tests"], command: `node '${join(repository, "tools/component-build/test-node.mjs")}' tests` };
}
test("D selected contract execution runs owning cases without frontend, database or model setup", context => {
  const fixture = contractFixture(context);
  fixture.component.tests = [nodeSuite(fixture)];
  const result = invokeLane(fixture, ["--component", "game-evidence-contracts"], { TEMPO_COMPONENT_PYTHON: "/python-must-not-run" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /actual selected contract fixture/);
  assert.equal(readFileSync(join(fixture.root, "contracts/game-evidence/executions"), "utf8"), "case\n");
  assert.equal(existsSync(join(fixture.root, "app")), false);
});
test("D shared suites dispatch once when selected repeatedly", context => {
  const fixture = contractFixture(context);
  fixture.manifest.suites = [{ id: "shared", ...nodeSuite(fixture) }];
  const result = invokeLane(fixture, ["--suite", "shared", "--suite", "shared"]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(readFileSync(join(fixture.root, "contracts/game-evidence/executions"), "utf8"), "case\n");
});
test("D zero-case execution cannot succeed through an empty owning runner", context => {
  const fixture = contractFixture(context);
  fixture.component.tests[0].command = "node -e 'process.exit(0)' --";
  const result = invokeLane(fixture, ["--component", "game-evidence-contracts"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /zero executed|suite failed/i);
});
test("D checker and restricted builder listing never invoke a toolchain", context => {
  const fixture = contractFixture(context);
  fixture.save();
  for (const entrypoint of ["check-components.mjs", "restricted-build.mjs"]) {
    const result = spawnSync(process.execPath, [join(repository, "tools/component-build", entrypoint), "--component", "game-evidence-contracts", "--list"], { encoding: "utf8", env: { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" } });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /game-evidence-contracts/);
  }
  assert.equal(existsSync(join(fixture.root, "test-results")), false);
});

test('retired standalone Learning owner is rejected instead of silently selecting another suite', async () => {
  const {selectComponents, selectSuites} = await import('../selection.mjs');
  assert.throws(() => selectComponents(original, ['learning']), /Unknown component: learning/);
  assert.throws(() => selectSuites(original, ['learning']), /Unknown suite: learning/);
});
