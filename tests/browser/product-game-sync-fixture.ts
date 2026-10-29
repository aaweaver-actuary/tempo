import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve, relative, isAbsolute } from "node:path";
import type { APIRequestContext } from "@playwright/test";
import { postgresFixtureComposeArguments, verifyPostgresFixtureTarget } from "./product-fixture-reset";
import { withDevelopmentStartup } from "../../scripts/development-budget.mjs";

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
  const secretRoot = process.env.TEMPO_PG_TEST_SECRETS;
  if (!secretRoot) throw new Error("The owned fixture's managed Compose override is required");
  const override = resolve(process.env.TEMPO_TEST_FIXTURE_COMPOSE_OVERRIDE ?? join(secretRoot, "resource-limits.json"));
  const inside = relative(resolve(secretRoot), override);
  if (!inside || inside.startsWith("..") || isAbsolute(inside) || !existsSync(override))
    throw new Error("Managed fixture override must be inside its existing owned secret directory");
  const argumentsList = [...postgresFixtureComposeArguments(project), "-f", override, "run", "--rm", "--no-deps",
    "--volume", `${resolve(".")}:/source:ro`, "--entrypoint", "python", "foreground-worker",
    "/source/backend/tests/fixtures/product_game_sync_browser.py", action, "--project", project!];
  if (fixture) argumentsList.push("--fixture-json", JSON.stringify(fixture));
  const result = withDevelopmentStartup(["foreground-worker"], project!, () => ({
    status: 0,
    stdout: execFileSync("docker", argumentsList, { encoding: "utf8", timeout: 120_000, maxBuffer: 256 * 1024 }),
  }), { oneOff: true });
  return JSON.parse(result.stdout.trim().split("\n").at(-1)!);
}
