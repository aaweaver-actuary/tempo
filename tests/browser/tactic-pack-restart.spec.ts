import { execFileSync } from "node:child_process";
import type { APIRequestContext, APIResponse } from "@playwright/test";
import { api, expect, move, nav, test } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";
import { verifyPostgresFixtureTarget } from "./product-fixture-reset";
import { assertDevelopmentProjectOwned, validateDevelopmentBudgetOwner } from "../../scripts/development-budget.mjs";

const packId = "hangingPiece-easy-01";

async function confirmed(request: APIRequestContext, response: APIResponse) {
  const body = await response.json();
  expect(response.ok(), JSON.stringify(body)).toBe(true);
  if (response.status() !== 202) return body;
  let result: { state: string; response?: unknown };
  await expect.poll(async () => {
    result = await (await request.get(`${api}/operations/${body.operation_id}`)).json();
    return result.state;
  }, { timeout: 30_000 }).toBe("complete");
  return result!.response as typeof body;
}

test("pack activation and solved progress survive an owned service restart; missing pack leaves Study playable", async ({ page, request, baseURL }) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  const origin = process.env.TEMPO_DOCKER_URL;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project) || !origin || baseURL !== origin || api !== `${origin}/api`)
    throw new Error("This restart requires the owning disposable PostgreSQL browser runner and origin");
  await validateDevelopmentBudgetOwner();
  const health = await (await request.get(`${api}/health`)).json();
  await verifyPostgresFixtureTarget({ health, apiUrl: api, project });

  const imported = await confirmed(request, await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "pack-restart.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Pack restart"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *') },
    trained_color: "white", initial_depth: "2",
  } }));
  expect(imported.repertoire_id).toBeTruthy();
  await expect.poll(async () => (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards
    .some((card: { repertoire_id?: string }) => card.repertoire_id === imported.repertoire_id), { timeout: 30_000 }).toBe(true);

  const deck = await (await request.get(`/data/tactics-packs/${packId}.json`)).json() as { PuzzleId: string; DeckPosition: number; Moves: string }[];
  const before = await (await request.get(`${api}/tactics/progress`)).json();
  const seen = new Set<string>(before[packId]?.discoveredIds ?? []);
  const puzzle = deck.find(item => !seen.has(`lichess-${item.PuzzleId}`));
  expect(puzzle).toBeTruthy();
  await prepareUI(page);
  await nav(page, "Tactics");
  await page.getByRole("tab", { name: "Packs", exact: true }).click();
  const catalog = page.getByRole("region", { name: "Tactical puzzle catalog" });
  const activate = catalog.getByRole("button", { name: "Activate Hanging pieces easy pack 1" });
  if (!(await activate.isVisible())) await catalog.locator(".tactic-theme summary").filter({ hasText: "Hanging pieces" }).click();
  await activate.click();
  await expect(catalog.getByRole("button", { name: "Deactivate Hanging pieces easy pack 1" })).toBeVisible();
  await page.getByRole("tab", { name: "Solve", exact: true }).click();
  await expect(page.getByText(`Puzzle ${puzzle!.DeckPosition} of 25`)).toBeVisible();
  const moves = puzzle!.Moves.split(/\s+/);
  for (let index = 1; index < moves.length; index += 2) await move(page, moves[index].slice(0, 2), moves[index].slice(2, 4));
  await expect.poll(async () => (await (await request.get(`${api}/tactics/progress`)).json())[packId]?.cleanIds ?? [], { timeout: 30_000 })
    .toContain(`lichess-${puzzle!.PuzzleId}`);
  const savedProgress = await (await request.get(`${api}/tactics/progress`)).json();

  // Restart only already-running containers owned by this runner's budget token.
  const owned = assertDevelopmentProjectOwned(project) as { Id: string; Config: { Labels: Record<string, string> } }[];
  const restartIds = ["api", "web"].map(service => {
    const matches = owned.filter(item => item.Config.Labels["com.docker.compose.service"] === service);
    if (matches.length !== 1 || !/^[a-f0-9]{12,64}$/.test(matches[0].Id)) throw new Error(`Ambiguous owned ${service} container`);
    return matches[0].Id;
  });
  for (const id of restartIds) execFileSync("docker", ["restart", "--time", "20", id], { timeout: 45_000 });
  await expect.poll(async () => {
    try { return (await request.get(`${api}/health`)).ok(); } catch { return false; }
  }, { timeout: 30_000 }).toBe(true);
  expect((await (await request.get(`${api}/tactics/progress`)).json())[packId]).toEqual(savedProgress[packId]);
  expect((await (await request.get(`${api}/tactics/catalog`)).json()).packs.find((pack: { id: string }) => pack.id === packId).active).toBe(true);
  await page.reload();
  await nav(page, "Tactics");
  await page.getByRole("tab", { name: "Packs", exact: true }).click();
  const deactivate = page.getByRole("button", { name: "Deactivate Hanging pieces easy pack 1" });
  if (!(await deactivate.isVisible())) await page.locator(".tactic-theme summary").filter({ hasText: "Hanging pieces" }).click();
  await deactivate.click();
  await expect.poll(async () => (await (await request.get(`${api}/tactics/catalog`)).json()).packs
    .find((pack: { id: string }) => pack.id === packId).active).toBe(false);

  // Fail the actual static pack request, then navigate back to the real Study queue.
  await page.route(`**/data/tactics-packs/${packId}.json`, route => route.abort("failed"));
  await page.reload();
  await nav(page, "Tactics");
  await expect(page.getByText(/Could not load|No validated puzzles/i).first()).toBeVisible();
  await nav(page, "Train");
  await expect(page.locator("#train")).toBeVisible();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
  const opening = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards[0];
  expect(opening.repertoire_id).toBe(imported.repertoire_id);
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
});
