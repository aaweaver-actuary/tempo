import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { api, expect, move, test } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

test("imported miss retains one lapse then ordinary Study saves and settles with producer transport disabled", async ({ page, request }) => {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("Product feedback requires the owning disposable PostgreSQL runner");
  const identity = `product-feedback-${randomUUID().replaceAll("-", "")}`;
  const fixture = (action: string) => JSON.parse(execFileSync("docker", ["compose", "-p", project,
    "-f", "docker-compose.postgres.test.yml", "run", "--rm", "--no-deps", "schema", "python",
    "/source/backend/tests/fixtures/product_feedback.py", action, identity], { encoding: "utf8", timeout: 30_000 }));
  const facts = fixture("seed");
  const before = await (await request.get(`${api}/queue/window?limit=20`)).json();
  expect(before.cards[0].id).toBe(facts.protected_id);
  const operation = randomUUID();
  const submit = async () => {
    const response = await request.post(`${api}/game-evidence/product/feedback`, {
      headers: { "Idempotency-Key": operation },
      data: { game_id: facts.game_id, repertoire_id: facts.repertoire_id, occurrence_ply: 1 },
    });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const key = new URLSearchParams({ provider: "lichess", game_id: facts.game_id,
    decision_id: facts.decision_id, occurrence_ply: "1" });
  const status = async () => {
    const response = await request.get(`${api}/game-evidence/product/effect?${key}`);
    return response.ok() ? response.json() : null;
  };
  await submit();
  await expect.poll(async () => (await status())?.status, { timeout: 30_000 }).toBe("applied");
  const admitted = await status();
  await submit();
  expect((await status()).owner_receipt_id).toBe(admitted.owner_receipt_id);
  expect((await status()).available_by).toBe(admitted.available_by);
  // Disable the optional producer surface; every Study request remains real.
  await page.route("**/api/game-evidence/product/feedback", route => route.abort());
  await page.route("**/api/games/**", route => route.abort());
  await prepareUI(page);
  await expect(page.getByText("Protected unrelated", { exact: true }).first()).toBeVisible();
  const preserved = await (await request.get(`${api}/queue/window?limit=20`)).json();
  expect(preserved.cards[0].id).toBe(facts.protected_id);
  await move(page, "d2", "d4");
  await expect(page.getByText("Feedback target", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", facts.target_fen);
  const saved = page.waitForResponse(response => response.url().includes(`/api/cards/${facts.target_id}/review`) && response.request().method() === "POST");
  await move(page, "e2", "e4");
  expect((await saved).ok()).toBe(true);
  await expect.poll(async () => (await status())?.status).toBe("settled");
  await page.reload();
  const settled = await status();
  expect(settled.status).toBe("settled");
  expect(settled.owner_receipt_id).toBe(admitted.owner_receipt_id);
  expect(settled.available_by).toBe(admitted.available_by);
  expect(fixture("readback")).toEqual({ lapses: 1, study_reviews: 2, review_receipts: 1, status: "settled" });
});
