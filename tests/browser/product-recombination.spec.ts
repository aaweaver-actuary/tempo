import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { Chess } from "chess.js";
import { api, expect, move, test } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

type Stage = { card_id: string; queue_entry_id: number; starting_fen: string; moves: string[] };
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
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id",String(staged.queue_entry_id));
    await expect.poll(async () => (await page.locator(".board-frame").first().getAttribute("data-fen"))?.split(" ").slice(0,3).join(" "))
      .toBe(staged.starting_fen.split(" ").slice(0,3).join(" "));
    if (failed) await page.getByRole("button", { name: /Show move/ }).first().click();
    // The real board supplies every learner response; no Study API is mocked.
    const color = staged.starting_fen.split(" ")[1];
    const expectedPosition = new Chess(staged.starting_fen);
    for (const uci of staged.moves) {
      if (expectedPosition.turn() === color) {
        // Input can be enabled before the scheduled opponent reply. Wait for
        // that actual position rather than clicking a later response early.
        await expect.poll(async () => (await page.locator(".board-frame").first().getAttribute("data-fen"))?.split(" ").slice(0,3).join(" "))
          .toBe(expectedPosition.fen().split(" ").slice(0,3).join(" "));
        await move(page, uci.slice(0, 2), uci.slice(2, 4));
      }
      expectedPosition.move({ from: uci.slice(0,2), to: uci.slice(2,4), promotion: uci.slice(4) || undefined });
    }
    // Persistence is authoritative even when the client sends through its
    // worker; a Page response event alone does not cover that transport.
    await expect.poll(() => fixture<Readback>("readback").study_counts[staged.card_id],{timeout:15_000}).toBe(before+1);
    await expect(page.locator("#train")).not.toHaveAttribute("data-queue-entry-id",String(staged.queue_entry_id));
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
  // The existing UI teaches a presentation with no prior whole-sequence
  // review. That genuine assisted pass earns no clean evidence; it supplies
  // no manufactured history or first-correct fact for the new composite.
  await complete("combined");
  expect(fixture<Readback>("readback").generations.find(generation => generation.presentation_id === ab.presentation_id)?.clean_count).toBe(0);
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
  const splitStage=fixture<Stage>("practice", "combined");
  await page.reload();
  await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id",String(splitStage.queue_entry_id));
  await expect(page.getByRole("button", { name: "Accept", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  await expect.poll(() => fixture<Readback>("readback").changes,{timeout:15_000}).toBe(3);
  await page.reload();
  state = fixture<Readback>("readback");
  expect(state.changes).toBe(3);
  expect(state.generations.filter(generation => generation.lineage.includes(abc.presentation_id)))
    .toEqual(expect.arrayContaining([expect.objectContaining({ clean_count: 0, review_count: 0 })]));
  expect(state.original_cards).toBe(3);
  expect(state.unknown_history).toBe(3);
  expect(state.protected).toEqual(protectedOrigin);
  const queue = await (await request.get(`${api}/queue/window?limit=50`)).json();
  expect(queue.cards.some((card: { id: string }) => card.id === abc.presentation_id)).toBe(false);
  await other.close();
});
