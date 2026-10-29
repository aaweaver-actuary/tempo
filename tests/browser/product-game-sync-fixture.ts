import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { APIRequestContext } from "@playwright/test";
import { postgresFixtureComposeArguments, verifyPostgresFixtureTarget } from "./product-fixture-reset";

export type GameSyncFixture = {
  project: string; repertoire_id: string; card_id: string; game_id: string;
  source_id: string; start_fen: string; played_at: string; token: string;
  unrelated_card: string; unrelated_entry: number; unrelated_fen: string;
};

export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "seed", fixture?: undefined): Promise<GameSyncFixture>;
export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "run" | "inspect" | "refresh" | "worker-stop" | "worker-kill", fixture: GameSyncFixture): Promise<Record<string, unknown>>;
export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "seed" | "run" | "inspect" | "refresh" | "worker-stop" | "worker-kill", fixture?: GameSyncFixture) {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const healthResponse = await request.get(`${api}/health`);
  if (!healthResponse.ok()) throw new Error("Disposable API health unavailable");
  await verifyPostgresFixtureTarget({ health: await healthResponse.json(), apiUrl: api, project });
  postgresFixtureComposeArguments(project);
  const providerDirectory = process.env.TEMPO_PROVIDER_FIXTURE_DIR;
  if (!providerDirectory || !resolve(providerDirectory).startsWith(resolve(".tempo-pg-test-secrets-491-reader-").slice(0, -1)))
    throw new Error("Actual packaged provider fixture directory is required");
  if (action === "worker-stop" || action === "worker-kill") {
    const worker = JSON.parse(execFileSync("docker", ["inspect", "--format",
      '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"running":{{json .State.Running}},"cpu":{{json .HostConfig.NanoCpus}},"memory":{{json .HostConfig.Memory}},"pids":{{json .HostConfig.PidsLimit}},"command":{{json .Config.Cmd}}}',
      `${project}-game-feedback-worker-1`], { encoding: "utf8", timeout: 5_000 }));
    if (!/^[a-f0-9]{64}$/.test(worker.id) || !worker.running ||
        worker.labels["com.docker.compose.project"] !== project || worker.labels["com.docker.compose.service"] !== "game-feedback-worker" ||
        worker.labels["tempo.resource-owner"] !== process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN ||
        worker.cpu !== 0.15e9 || worker.memory !== 256 * 1024 ** 2 || worker.pids !== 96 ||
        JSON.stringify(worker.command) !== JSON.stringify(["python", "-m", "app.product_composition.game_sync_current", "--workspace", "legacy"]))
      throw new Error("Only this owned actual optional worker may be signalled");
    // Docker kill marks a container manually stopped and suppresses its real
    // restart policy. Signal the exact CLI child of init instead; no other
    // process, service, startup journal or allocation is touched.
    const signal = action === "worker-stop" ? "SIGTERM" : "SIGKILL";
    const source = `import os, signal\nfrom pathlib import Path\nexpected = [b'python', b'-m', b'app.product_composition.game_sync_current', b'--workspace', b'legacy']\nchildren = Path('/proc/1/task/1/children').read_text().split()\nworkers = [int(pid) for pid in children if Path('/proc/' + pid + '/cmdline').read_bytes().rstrip(b'\\0').split(b'\\0') == expected]\nassert len(workers) == 1, 'Exact optional CLI child required'\nos.kill(workers[0], signal.${signal})\n`;
    execFileSync("docker", ["exec", worker.id, "python", "-c", source], { timeout: 10_000 });
    return { signalled: action, container: worker.id };
  }
  // Execute inside the existing capped writer slot. Starting another Compose
  // service would require the parent runner's process-owned startup journal.
  const metadata = JSON.parse(execFileSync("docker", ["inspect", "--format",
    '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"running":{{json .State.Running}},"cpu":{{json .HostConfig.NanoCpus}},"memory":{{json .HostConfig.Memory}}}',
    `${project}-foreground-worker-1`], { encoding: "utf8", timeout: 5_000 }));
  const owner = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
  if (!owner || !/^[a-f0-9]{64}$/.test(metadata.id) || !metadata.running ||
      metadata.labels["com.docker.compose.project"] !== project ||
      metadata.labels["com.docker.compose.service"] !== "foreground-worker" ||
      metadata.labels["tempo.resource-owner"] !== owner || metadata.labels["tempo.resource-class"] !== "development" ||
      metadata.cpu <= 0 || metadata.cpu > 0.35e9 || metadata.memory <= 0 || metadata.memory > 192 * 1024 ** 2)
    throw new Error("Game sync fixture requires its existing owned and capped writer container");
  const argumentsList = ["exec", "-i", metadata.id, "python", "-", action, "--project", project!];
  if (fixture) argumentsList.push("--fixture-json", JSON.stringify(fixture));
  const result = spawnSync("docker", argumentsList, { encoding: "utf8", timeout: 120_000, maxBuffer: 256 * 1024,
    input: readFileSync(resolve("backend/tests/fixtures/product_game_sync_browser.py"), "utf8") });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Owned game sync fixture failed: ${result.stderr}`);
  const value = JSON.parse(result.stdout.trim().split("\n").at(-1)!);
  if (action === "seed") {
    writeFileSync(resolve(providerDirectory, "providers.json"), JSON.stringify(value.http_fixtures), { mode: 0o600 });
    delete value.http_fixtures;
  }
  if (action === "run") {
    writeFileSync(resolve(providerDirectory, "worker-ready"), project!, { mode: 0o600 });
    value.http = JSON.parse(readFileSync(resolve(providerDirectory, "http-state.json"), "utf8"));
  }
  return value;
}
