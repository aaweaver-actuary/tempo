// @vitest-environment node
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { expect, it } from "vitest";

// A real task-local budget capability, consumed by the unchanged managed adapter.
function managedFixture(directory: string) {
  const budget = join(directory, "budget");
  mkdirSync(join(budget, "resources"), { recursive: true });
  const token = "visual-regression-owner";
  writeFileSync(join(budget, "owner.json"), JSON.stringify({ token, pid: process.pid }));
  const docker = join(directory, "docker");
  const calls = join(directory, "docker-calls.jsonl");
  writeFileSync(docker, `#!${process.execPath}
import { appendFileSync, readFileSync, readdirSync } from 'node:fs';
const args=process.argv.slice(2);
const owner=JSON.parse(readFileSync(${JSON.stringify(join(budget, "owner.json"))},'utf8'));
if(owner.token!==process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)throw new Error('invalid capability');
appendFileSync(${JSON.stringify(calls)},JSON.stringify(args)+'\\n');
if(args[0]==='info') console.log('4 4294967296');
else if(args[0]==='ps') {}
else if(args[0]==='container'&&args[1]==='inspect') {
 console.error(process.env.TEMPO_FAKE_INSPECT_UNKNOWN ? 'daemon unavailable' : 'No such container: '+args.at(-1)); process.exitCode=1;
} else if(args[0]==='run') {
 const name=args[args.indexOf('--name')+1];
 if(!readdirSync(${JSON.stringify(join(budget, "resources"))}).includes(encodeURIComponent('container:'+name)))throw new Error('missing resource journal');
} else throw new Error('unexpected docker command '+JSON.stringify(args));
`);
  chmodSync(docker, 0o700);
  return { calls, budget, token, env: { ...process.env,
    PATH: `${directory}:${process.env.PATH ?? ""}`, TEMPO_REAL_DOCKER: docker,
    TEMPO_DEVELOPMENT_BUDGET_TOKEN: token, TEMPO_DEVELOPMENT_BUDGET_DIRECTORY: budget,
    TEMPO_STUDY_ACTIVITY_LEGACY: "absent", TEMPO_STUDY_ACTIVITY_DIRECTORY: join(directory, "activity"),
    TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES: "[]" } };
}

it("pinned visual runner caches npm downloads while reinstalling locked dependencies", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "tempo-visual-runner-"));
  try {
    const fixture = managedFixture(temporaryDirectory);
    const run = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: fixture.env,
    });
    expect(run.status).toBe(0);
    const calls = readFileSync(fixture.calls, "utf8").trim().split("\n").map(line => JSON.parse(line) as string[]);
    const argumentsPassed = calls.find(args => args[0] === "run")!;
    expect(calls[0][0]).toBe("info");
    expect(argumentsPassed).toContain("tempo.resource-class=development");
    expect(argumentsPassed).toContain(`tempo.resource-owner=${fixture.token}`);
    expect(argumentsPassed).toContain("2048m");
    expect(argumentsPassed).toContain("--memory");
    expect(argumentsPassed).toContain("--pids-limit");
    expect(argumentsPassed).toContain("512");
    expect(argumentsPassed).not.toContain("--cpus");
    expect(argumentsPassed).toContain("--ipc=host");
    expect(argumentsPassed).toContain("--init");
    expect(argumentsPassed).toContain("TEMPO_DIAGNOSTIC_CONTAINER_LIMITS=memory=2048 MiB; pids=512; shared host IPC; unrestricted CPU; trial allocation, not prequalified");
    expect(readdirSync(join(fixture.budget, "resources"))).toEqual([]);
    expect(argumentsPassed).toContain("type=volume,source=tempo-playwright-npm-cache,target=/root/.npm");
    expect(argumentsPassed).toContain("/workspace/node_modules");
    expect(argumentsPassed.some((argument) => argument.includes("npm ci --no-audit"))).toBe(true);
    expect(argumentsPassed).toContain("linux/arm64");
    expect(argumentsPassed.some(argument => /^TEMPO_BROWSER_BUILD_DIR=\/workspace\/test-results\/tempo-local-build-[^/]+\/output$/.test(argument))).toBe(true);
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});

it("held_drag_nested_performance_manifest_preserves_the_full_run_timestamp", () => {
  const commandDirectory = mkdtempSync(join(tmpdir(), "tempo-nested-runner-"));
  mkdirSync("test-results", { recursive: true });
  const outputDirectory = mkdtempSync(join(process.cwd(), "test-results/manifest-regression-"));
  try {
    // Stub expensive stage execution, but execute the real full runner and its
    // visual runner to verify their manifest contract without Docker or tests.
    const preload = join(commandDirectory, "stage-stub.mjs");
    const visualEnvironment = join(commandDirectory, "visual-environment.json");
    writeFileSync(preload, `import childProcess from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
childProcess.spawnSync = (command, args, options) => {
  if (args?.includes('test:visual')) writeFileSync(process.env.TEMPO_VISUAL_ENVIRONMENT, JSON.stringify(options.env));
  return { status: 0, stdout: 'synthetic-version', stderr: '' };
};
syncBuiltinESMExports();\n`);
    const fixture = managedFixture(commandDirectory);
    const fullRun = spawnSync(process.execPath, ["--import", preload, "scripts/test-all.mjs", "full"], {
      encoding: "utf8", env: { ...fixture.env, TEMPO_TEST_TIMING_DIR: outputDirectory,
        TEMPO_VISUAL_ENVIRONMENT: visualEnvironment },
    });
    expect(fullRun.status, fullRun.stderr).toBe(0);
    const fullManifest = JSON.parse(readFileSync(join(outputDirectory, "test-stages-full.json"), "utf8"));
    const nestedEnvironment = JSON.parse(readFileSync(visualEnvironment, "utf8"));
    const nestedRun = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      encoding: "utf8", env: { ...nestedEnvironment, PATH: `${commandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(nestedRun.status, nestedRun.stderr).toBe(0);
    const nestedManifest = JSON.parse(readFileSync(join(outputDirectory, "performance-run.json"), "utf8"));
    expect(nestedManifest.timestamp).toBe(fullManifest.timestamp);
    expect(nestedManifest.commit).toBe(fullManifest.commit);
    const standaloneEnvironment = { ...nestedEnvironment };
    delete standaloneEnvironment.TEMPO_FULL_TEST_RUN_TIMESTAMP;
    delete standaloneEnvironment.TEMPO_FULL_TEST_RUN_COMMIT;
    const standaloneRun = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      encoding: "utf8", env: { ...standaloneEnvironment, PATH: `${commandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(standaloneRun.status, standaloneRun.stderr).toBe(0);
    const standaloneManifest = JSON.parse(readFileSync(join(outputDirectory, "performance-run.json"), "utf8"));
    expect(Date.parse(standaloneManifest.timestamp)).toBeGreaterThan(Date.parse(fullManifest.timestamp));
  } finally {
    rmSync(outputDirectory, { recursive: true, force: true });
    rmSync(commandDirectory, { recursive: true, force: true });
  }
});

it("pinned visual runner rejects stale ownership before Docker and retains uncertain cleanup journals", () => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-visual-ownership-"));
  try {
    const fixture = managedFixture(directory);
    const stale = spawnSync(process.execPath, ["scripts/test-visual.mjs"], {
      encoding: "utf8", env: { ...fixture.env, TEMPO_DEVELOPMENT_BUDGET_TOKEN: "stale" },
    });
    expect(stale.status).not.toBe(0);
    expect(() => readFileSync(fixture.calls)).toThrow();
    const uncertain = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      encoding: "utf8", env: { ...fixture.env, TEMPO_FAKE_INSPECT_UNKNOWN: "1" },
    });
    expect(uncertain.status).not.toBe(0);
    expect(readdirSync(join(fixture.budget, "resources"))).toHaveLength(1);
    expect(JSON.parse(readFileSync(join(fixture.budget, "owner.json"), "utf8")).token).toBe(fixture.token);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

it.each(["info", "run"])("pinned visual budget parent cancellation retains rehearsal ownership during %s", async (blockedCommand) => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-visual-cancellation-"));
  const budget = join(directory, "budget");
  const ready = join(directory, "ready.json");
  const release = join(directory, "release");
  const docker = join(directory, "docker");
  let child: ReturnType<typeof spawn> | undefined;
  let completion: Promise<number | null> | undefined;
  let blocked: { pid: number; wrapperPid: number; wrapper: string; build?: string } | undefined;
  const waitUntil = async (condition: () => boolean) => {
    const deadline = Date.now() + 10000;
    while (!condition()) {
      if (Date.now() > deadline) throw new Error("Task-owned fake Docker handoff timed out");
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  try {
    writeFileSync(docker, `#!${process.execPath}
import {existsSync,writeFileSync} from 'node:fs';
const args=process.argv.slice(2);
if(args[0]===${JSON.stringify(blockedCommand)}) {
 const build=args.find(value=>value.startsWith('TEMPO_BROWSER_BUILD_DIR='))?.slice('TEMPO_BROWSER_BUILD_DIR=/workspace/'.length);
 writeFileSync(${JSON.stringify(ready)},JSON.stringify({pid:process.pid,wrapperPid:process.ppid,wrapper:process.env.PATH.split(':')[0],build}));
 while(!existsSync(${JSON.stringify(release)})) await new Promise(resolve=>setTimeout(resolve,20));
}
if(args[0]==='info')console.log('4 4294967296');
else if(args[0]==='container'&&args[1]==='inspect'){console.error('No such container: '+args.at(-1));process.exitCode=1;}
else if(!['ps','run'].includes(args[0]))throw new Error('Unexpected fake Docker command');
`);
    chmodSync(docker, 0o700);
    const env: NodeJS.ProcessEnv = { ...process.env, PATH: `${directory}:${process.env.PATH ?? ""}`,
      TEMPO_REAL_DOCKER: docker, TEMPO_DEVELOPMENT_BUDGET_DIRECTORY: budget,
      TEMPO_STUDY_ACTIVITY_LEGACY: "absent", TEMPO_STUDY_ACTIVITY_DIRECTORY: join(directory, "activity"),
      TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES: "[]" };
    delete env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
    child = spawn(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], { env, stdio: "ignore" });
    completion = new Promise(resolve => child!.once("exit", code => resolve(code)));
    await waitUntil(() => existsSync(ready));
    blocked = JSON.parse(readFileSync(ready, "utf8"));
    const before = readdirSync(join(budget, "resources")).map(decodeURIComponent);
    expect(before.some(key => key.startsWith("rehearsal:visual-"))).toBe(true);
    expect(before.some(key => key.startsWith("container:"))).toBe(blockedCommand === "run");
    child.kill("SIGTERM");
    expect(await completion).not.toBe(0);
    expect(existsSync(join(budget, "owner.json"))).toBe(true);
    expect(readdirSync(join(budget, "resources")).map(decodeURIComponent)).toEqual(before);
  } finally {
    // Release only this fixture's fake process; never reconcile unknown real work.
    if (!blocked && existsSync(ready)) blocked = JSON.parse(readFileSync(ready, "utf8"));
    writeFileSync(release, "release");
    if (child && child.exitCode === null) child.kill("SIGTERM");
    await completion;
    if (blocked) {
      await waitUntil(() => { try { process.kill(blocked!.pid, 0); return false; } catch { return true; } });
      // Wait for the exact wrapper to finish its absence verification.
      await waitUntil(() => { try { process.kill(blocked!.wrapperPid, 0); return false; } catch { return true; } });
      rmSync(dirname(blocked.wrapper), { recursive: true, force: true });
      if (blocked.build) rmSync(dirname(join(process.cwd(), blocked.build)), { recursive: true, force: true });
    }
    rmSync(directory, { recursive: true, force: true });
  }
}, 20000);
