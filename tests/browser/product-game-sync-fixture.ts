import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import type { APIRequestContext } from "@playwright/test";
import { postgresFixtureComposeArguments, verifyPostgresFixtureTarget } from "./product-fixture-reset";

const execute = promisify(execFile);
export type GameSyncFixture = {
  project: string; repertoire_id: string; card_id: string; game_id: string;
  source_id: string; start_fen: string; played_at: string; token: string;
  unrelated_card: string; unrelated_entry: number; unrelated_fen: string;
};

export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "seed", fixture?: undefined): Promise<GameSyncFixture>;
export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "run" | "inspect" | "refresh", fixture: GameSyncFixture): Promise<Record<string, unknown>>;
export async function gameSyncFixture(request: APIRequestContext, api: string,
  action: "seed" | "run" | "inspect" | "refresh", fixture?: GameSyncFixture) {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const healthResponse = await request.get(`${api}/health`);
  if (!healthResponse.ok()) throw new Error("Disposable API health unavailable");
  await verifyPostgresFixtureTarget({ health: await healthResponse.json(), apiUrl: api, project });
  const argumentsList = [...postgresFixtureComposeArguments(project), "run", "--rm", "--no-deps",
    "--volume", `${resolve(".")}:/source:ro`, "--entrypoint", "python", "foreground-worker",
    "/source/backend/tests/fixtures/product_game_sync_browser.py", action, "--project", project!];
  if (fixture) argumentsList.push("--fixture-json", JSON.stringify(fixture));
  const result = await execute("docker", argumentsList, { encoding: "utf8", timeout: 120_000, maxBuffer: 256 * 1024 });
  return JSON.parse(result.stdout.trim().split("\n").at(-1)!);
}
