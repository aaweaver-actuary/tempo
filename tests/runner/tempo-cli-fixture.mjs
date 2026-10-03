import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { targetKey } from "../../scripts/tempo-deployment.mjs";

export function cliFixture(mode = "upgrade") {
  const directory = mkdtempSync(join(tmpdir(), "tempo-cli-command-"));
  const root = join(directory, "checkout"); const bin = join(directory, "bin");
  mkdirSync(join(root, "backend/app"), { recursive: true }); mkdirSync(bin);
  writeFileSync(join(root, "backend/app/schema_version.py"), "POSTGRES_SCHEMA_VERSION = 29\n");
  const secret = join(directory, "password"); writeFileSync(secret, "canary-private-password", { mode: 0o600 });
  const names = ["postgres", "redis", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "defense-engine", "maia-worker", "postgres-backup", "migration"];
  const volumes = Object.fromEntries(["tempo-postgres-data", "tempo-postgres-backups", "tempo-redis-data", "tempo-engine-operations"].map(name => [name, { name, external: true }]));
  const services = Object.fromEntries(names.map(name => [name, { image: `fixture-${name}`, ...(name !== "postgres" && name !== "redis" && name !== "postgres-backup" ? { build: { context: root } } : {}) }]));
  services.postgres = { image: "postgres:18.6-trixie", environment: { POSTGRES_USER: "tempo", POSTGRES_DB: "tempo" },
    volumes: [{ type: "volume", source: "tempo-postgres-data", target: "/var/lib/postgresql" }] };
  services.redis.volumes = [{ type: "volume", source: "tempo-redis-data", target: "/data" }];
  services["postgres-backup"].volumes = [{ type: "volume", source: "tempo-postgres-backups", target: "/backups" }];
  services["defense-engine"].volumes = [{ type: "volume", source: "tempo-engine-operations", target: "/state" }];
  services.api.environment = { TEMPO_DATABASE_READ_URL: "postgresql://tempo_reader@postgres:5432/tempo" };
  const config = { name: "tempo", volumes, services, secrets: { password: { file: secret } } };
  const composeFile = join(root, "compose.json"); writeFileSync(composeFile, JSON.stringify(config));
  const target = { version: 1, root, project: "tempo", context: "desktop-linux", daemonId: "fixture-daemon", ports: [], volumes,
    composeFiles: [composeFile], postgresVolumeKey: "tempo-postgres-data", postgresImage: "postgres:18.6-trixie",
    postgresMajor: 18, webUrl: "http://127.0.0.1:13000" };
  const registration = join(directory, "config.json"); writeFileSync(registration, JSON.stringify(target));
  const stateRoot = join(directory, "state"); const stateDirectory = join(stateRoot, targetKey(target));
  mkdirSync(stateDirectory, { recursive: true });
  const revision = "a".repeat(40);
  const oldRevision = mode === "upgrade" || mode.includes("fail") ? "b".repeat(40) : revision;
  const images = Object.fromEntries(names.map(name => [name, `sha256:fixture-${name}`]));
  const imageOverride = join(directory, "recorded-images.json");
  writeFileSync(imageOverride, JSON.stringify({ services: Object.fromEntries(names.map(name => [name, { image: images[name] }])) }));
  writeFileSync(join(stateDirectory, "deployment.json"), JSON.stringify({ version: 1, revision: oldRevision,
    schema: mode === "upgrade" || mode === "migration-fail" ? 28 : 29, images, composeFiles: [composeFile], imageOverride,
    evidence: { commit: oldRevision, url: "https://github.com/fixture/verified" }, verified_at: "2026-10-01" }));
  writeFileSync(join(directory, "fixture.json"), JSON.stringify({ mode, root, names, target, config, revision }));
  writeFileSync(join(directory, "machine.json"), JSON.stringify({ schema: mode === "upgrade" || mode === "migration-fail" ? 28 : 29,
    head: ["race-dirty", "race-head"].includes(mode) ? "c".repeat(40) : revision,
    containers: ["postgres", "redis"].map(name => ({ Id: `container-${name}`, Image: images[name],
      Config: { Image: `untrusted-tag-${name}`, Labels: { "com.docker.compose.project": "tempo",
        "com.docker.compose.service": name, "com.docker.compose.project.working_dir": root,
        "com.docker.compose.config-hash": `fixture-hash-${name}` } },
      Mounts: services[name].volumes.map(volume => ({ Type: "volume", Name: volume.source, Destination: volume.target })) })),
    running: names.filter(name => name !== "migration"), migrations: 0 }));
  for (const name of ["docker", "git"]) {
    const path = join(bin, name);
    writeFileSync(path, `#!${process.execPath}\n(${fakeCommand.toString()})();\n`); chmodSync(path, 0o755);
  }
  const hook = join(directory, "network.mjs");
  writeFileSync(hook, `import {readFileSync} from 'node:fs';\nconst fixture=JSON.parse(readFileSync(process.env.TEMPO_CLI_FIXTURE_DIRECTORY+'/fixture.json','utf8'));\nglobalThis.fetch=async(url)=>{\nif(String(url).includes('/actions/workflows/')) return Response.json({workflow_runs:[{id:12,head_sha:fixture.revision,head_branch:'main',event:'push',status:'completed',html_url:'https://github.com/fixture/ci'}]});\nif(String(url).includes('/actions/runs/')) { if(['race-dirty','race-head'].includes(fixture.mode)) { const path=process.env.TEMPO_CLI_FIXTURE_DIRECTORY+'/machine.json'; const machine=JSON.parse(readFileSync(path,'utf8')); if(fixture.mode==='race-dirty') machine.sourceEdited=true; else machine.head='d'.repeat(40); const fs=await import('node:fs'); fs.writeFileSync(path,JSON.stringify(machine)); if(fixture.mode==='race-dirty') fs.writeFileSync(fixture.root+'/personal-work','preserved study notes'); } return Response.json({jobs:['plan','frontend / verify','backend / verify','build / verify','postgres / verify','browser / verify','visual / verify','quality'].map(name=>({name,status:'completed',conclusion:fixture.mode==='ci-fail'&&name==='quality'?'failure':'success'}))}); }\nif(String(url).endsWith('/api/health')) return Response.json({status:'ok',storage:'postgresql',test_instance:false});\nreturn new Response('Tempo', {status:200});\n};\n`);
  const environment = { ...process.env, PATH: `${bin}:${process.env.PATH}`, TEMPO_CLI_FIXTURE_DIRECTORY: directory,
    TEMPO_CLI_STATE_DIR: stateRoot, TEMPO_CLI_CONFIG: registration };
  delete environment.TEMPO_UPGRADE_EXPECTED_PROJECT;
  const command = (...args) => spawnSync(process.execPath, ["--import", hook, "scripts/tempo-cli.mjs", ...args, "--config", registration], { encoding: "utf8", env: environment, timeout: 30_000 });
  const calls = () => exists(join(directory, "calls.jsonl")) ? readFileSync(join(directory, "calls.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line)) : [];
  return { directory, stateDirectory, command, calls, registration, environment, hook, target, root };
}

function exists(path) { try { readFileSync(path); return true; } catch { return false; } }

async function fakeCommand() {
  const fs = await import("node:fs"); const path = await import("node:path");
  const directory = process.env.TEMPO_CLI_FIXTURE_DIRECTORY;
  const fixture = JSON.parse(fs.readFileSync(path.join(directory, "fixture.json"), "utf8"));
  const machinePath = path.join(directory, "machine.json");
  const machine = JSON.parse(fs.readFileSync(machinePath, "utf8"));
  const args = process.argv.slice(2); const command = path.basename(process.argv[1]);
  fs.appendFileSync(path.join(directory, "calls.jsonl"), JSON.stringify({ command, args }) + "\n");
  const output = value => { console.log(typeof value === "string" ? value : JSON.stringify(value)); };
  const save = () => fs.writeFileSync(machinePath, JSON.stringify(machine));
  if (command === "git") {
    if (args[0] === "branch") output("main");
    else if (args[0] === "status") output(fixture.mode === "dirty" || machine.sourceEdited ? " M personal-work" : "");
    else if (args[0] === "remote") output("https://github.com/aaweaver-actuary/tempo");
    else if (args[0] === "rev-parse") output(args[1] === "HEAD" ? machine.head : fixture.revision);
    else if (args[0] === "merge") { machine.head = args.at(-1); save(); }
    else if (args[0] === "ls-remote") output(fixture.revision + " refs/heads/main");
    process.exit(0);
  }
  if (args.includes("info")) { if (args.includes("--format")) output("fixture-daemon"); process.exit(0); }
  if (args.includes("volume") && args.includes("inspect")) { output([]); process.exit(0); }
  if (args.includes("ps") && args.includes("-aq")) {
    const containers = args.includes("compose") ? machine.containers.filter(container => args.includes(container.Config.Labels["com.docker.compose.service"])) : machine.containers;
    output(containers.map(container => container.Id).join("\n")); process.exit(0);
  }
  if (args.includes("inspect") && !args.includes("image")) {
    output(machine.containers.filter(container => args.includes(container.Id))); process.exit(0);
  }
  if (args.includes("image") && args.includes("inspect")) {
    const image = args.at(-1); output([{ Id: image.startsWith("sha256:") ? image : `sha256:${image}`,
      Config: { Labels: { "org.opencontainers.image.revision": fixture.revision } } }]); process.exit(0);
  }
  if (!args.includes("compose") && args.includes("run")) { output(fixture.mode === "empty" ? "" : "18"); process.exit(0); }
  const resolvedConfig = () => {
    const config = structuredClone(fixture.config);
    for (let index = 0; index < args.length; index++) if (args[index] === "-f") {
      const extra = JSON.parse(fs.readFileSync(args[index + 1], "utf8"));
      for (const [name, service] of Object.entries(extra.services ?? {})) config.services[name] = { ...config.services[name], ...service,
        ...(service.build ? { build: { ...config.services[name]?.build, ...service.build } } : {}) };
    }
    return config;
  };
  if (args.includes("config")) {
    if (args.includes("--hash")) output(["postgres", "redis"].map(name => `${name} fixture-hash-${name}`).join("\n"));
    else output(resolvedConfig());
    process.exit(0);
  }
  if (args.includes("build") && fixture.mode === "build-fail") { console.error("build unavailable"); process.exit(13); }
  if (args.includes("build") && fixture.mode === "edited-during-build") { machine.sourceEdited = true; save(); }
  if (args.includes("ps")) { for (const Service of machine.running) output({ Service, State: "running" }); process.exit(0); }
  if (args.includes("stop")) { machine.running = machine.running.filter(name => !args.includes(name)); save(); process.exit(0); }
  if (args.includes("up")) {
    if (fixture.mode === "dependency-fail" && args.includes("postgres")) { console.error("dependency startup failed"); process.exit(18); }
    for (const name of ["postgres", "redis"].filter(name => args.includes(name))) {
      let container = machine.containers.find(container => container.Config.Labels["com.docker.compose.service"] === name);
      if (!container) { container = { Id: `container-${name}`, Config: { Labels: {} }, Mounts: [] }; machine.containers.push(container); }
      if (!args.includes("--no-recreate")) {
        const image = resolvedConfig().services[name].image;
        container.Image = image.startsWith("sha256:") ? image : `sha256:${image}`;
        container.Config.Labels = { "com.docker.compose.project": "tempo", "com.docker.compose.service": name,
          "com.docker.compose.project.working_dir": fixture.root, "com.docker.compose.config-hash": `fixture-hash-${name}` };
      }
    }
    machine.running = [...new Set([...machine.running, ...fixture.names.filter(name => args.includes(name))])]; save(); process.exit(0);
  }
  if (args.includes("ping")) { output("PONG"); process.exit(0); }
  if (args.includes("psql")) { output(Array.from({ length: machine.schema }, (_, index) => index + 1).join("\n")); process.exit(0); }
  if (args.includes("scripts/apply_postgres_migrations.py")) {
    if (args.includes("--check")) output({ expected_version: 29, applied_versions: Array.from({ length: machine.schema }, (_, index) => index + 1),
      pending_versions: machine.schema === 29 ? [] : [29], initialized: true, roles_ready: fixture.mode !== "status-fail", credentials_ready: true });
    else { machine.migrations++; save(); if (fixture.mode === "migration-fail") { console.error("migration failed"); process.exit(17); } machine.schema = 29; save(); }
    process.exit(0);
  }
  if (args.includes("scripts/verify_postgres_cli_state.py")) { output({ reviews: { count: 1, digest: "preserved" } }); process.exit(0); }
  if (fixture.mode === "backup-fail" && args.some(argument => argument.includes("pg_dump"))) { console.error("canary-private-password backup failed"); process.exit(14); }
  process.exit(0);
}
