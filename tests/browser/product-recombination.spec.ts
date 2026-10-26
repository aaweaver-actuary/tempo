import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { api, expect, move, test } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

type Stage = { card_id: string; starting_fen: string; moves: string[] };
type Readback = {
  generations: Array<{ presentation_id: string; clean_count: number; review_count: number; components: number; lineage: string[] }>;
  changes: number; original_cards: number; unknown_history: number; protected: unknown[];
  study_counts: Record<string, number>;
};

test("A08 normal Study joins only after saved clean reviews and retains confirmed splits and another issued attempt", async ({ page, request, context }) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("R11 requires the owning disposable PostgreSQL browser runner");
  const identity = `product-recombination-${randomUUID().replaceAll("-", "")}`;
  const fixture = <T>(action: string, ...args: string[]): T => JSON.parse(execFileSync("docker", ["compose", "-p", project,
    "-f", "docker-compose.postgres.test.yml", "run", "--rm", "--no-deps", "-e", "PYTHONPATH=/app", "schema", "python",
    "/source/backend/tests/fixtures/product_recombination.py", action, identity, ...args], { encoding: "utf8", timeout: 30_000 }));
  const facts = fixture<{ protected_id: string }>("seed");
  const other = await context.newPage();
  await prepareUI(other);
  await expect(other.getByText("Protected unrelated", { exact: true }).first()).toBeVisible();
  await expect.poll(() => fixture<Readback>("readback").protected?.[2]).toBe(1);
  const protectedOrigin = fixture<Readback>("readback").protected;
  await prepareUI(page);

  const complete = async (label: string, failed = false) => {
    const staged = fixture<Stage>("practice", label);
    const before = fixture<Readback>("readback").study_counts[staged.card_id] ?? 0;
    await page.reload();
    await expect.poll(async () => (await page.locator(".board-frame").first().getAttribute("data-fen"))?.split(" ").slice(0,3).join(" "))
      .toBe(staged.starting_fen.split(" ").slice(0,3).join(" "));
    const saved = page.waitForResponse(response => response.url().endsWith(`/api/cards/${staged.card_id}/review`)
      && response.request().method() === "POST");
    if (failed) await page.getByRole("button", { name: /Show move/ }).first().click();
    // The real board supplies every learner response; no Study API is mocked.
    const color = staged.starting_fen.split(" ")[1];
    for (let offset = color === "w" ? 0 : 1; offset < staged.moves.length; offset += 2) {
      const uci = staged.moves[offset];
      await move(page, uci.slice(0, 2), uci.slice(2, 4));
    }
    const response = await saved;
    expect(response.ok(), await response.text()).toBe(true);
    await expect.poll(() => fixture<Readback>("readback").study_counts[staged.card_id]).toBe(before+1);
    await expect.poll(() => fixture<Readback>("readback").protected).toEqual(protectedOrigin);
    return staged;
  };

  for (const label of ["A", "C", "B"]) {
    for (let review = 0; review < 3; review += 1) await complete(label);
  }
  let state = fixture<Readback>("readback");
  expect(state.changes).toBe(1);
  const ab = state.generations.find(generation => generation.components === 2)!;
  expect(ab.clean_count).toBe(0);
  expect(ab.review_count).toBe(0);
  for (let review = 0; review < 2; review += 1) {
    await complete("combined");
    expect(fixture<Readback>("readback").changes).toBe(1);
  }
  await complete("combined");
  state = fixture<Readback>("readback");
  const abc = state.generations.find(generation => generation.components === 3)!;
  expect(state.changes).toBe(2);
  expect(abc.clean_count).toBe(0);
  expect(abc.review_count).toBe(0);
  for (let review = 0; review < 3; review += 1) await complete("combined", true);
  fixture<Stage>("practice", "combined");
  await page.reload();
  await expect(page.getByRole("button", { name: "Accept", exact: true })).toBeVisible();
  const splitSaved = page.waitForResponse(response => response.url().endsWith(`/api/cards/${abc.presentation_id}/prefix-split`)
    && response.request().method() === "POST");
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  expect((await splitSaved).ok()).toBe(true);
  await page.reload();
  state = fixture<Readback>("readback");
  expect(state.changes).toBe(3);
  expect(state.generations.filter(generation => generation.lineage.includes(abc.presentation_id)))
    .toEqual(expect.arrayContaining([expect.objectContaining({ clean_count: 0, review_count: 0 })]));
  expect(state.original_cards).toBe(3);
  expect(state.unknown_history).toBe(1);
  expect(state.protected).toEqual(protectedOrigin);
  const queue = await (await request.get(`${api}/queue/window?limit=50`)).json();
  expect(queue.cards.some((card: { id: string }) => card.id === abc.presentation_id)).toBe(false);
  await other.close();
});
