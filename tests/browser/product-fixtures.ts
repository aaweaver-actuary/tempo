import { boardVisible, move } from "./native-board";
import { assertDisposableTarget } from "./disposable-target";
import { navigate } from "./ui-fixtures";
import type { Page } from "@playwright/test";
import { test as base, expect } from "./observability";
import { Chess } from "chess.js";
import { execFileSync } from "node:child_process";
import { resetPostgresDeletedCards } from "./product-fixture-reset";

const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
  ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");
const pgn =
  '[Event "Open game"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *\n\n[Event "Sicilian"]\n\n1. e4 c5 2. Nf3 d6 3. d4 *\n\n[Event "French"]\n\n1. e4 e6 2. d4 d5 3. Nc3 *';
const blackPgn =
  '[Event "King pawn"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n[Event "Queen pawn"]\n\n1. d4 d5 2. c4 e6 *\n\n[Event "English"]\n\n1. c4 e5 2. Nc3 Nf6 *';

async function nav(page: Page, name: string) {
  await navigate(page, name);
}
async function assertSharedBoardShellLayout(page: Page, mobile: boolean) {
  const shell = page.locator(".unified-board-shell-layout").first();
  await expect(shell).toBeVisible();
  const boardRegion = shell.locator(".persistent-board-shell").first();
  const panelRegion = shell.locator(".unified-board-shell-panel").first();
  await expect(boardRegion).toBeVisible();
  await expect(panelRegion).toBeVisible();
  const [boardBox, panelBox] = await Promise.all([
    boardRegion.boundingBox(),
    panelRegion.boundingBox(),
  ]);
  expect(boardBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  if (!boardBox || !panelBox) return;
  if (mobile)
    expect(boardBox.y + boardBox.height).toBeLessThanOrEqual(panelBox.y + 2);
  else expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(panelBox.x + 2);
}
async function clickSquare(
  page: Page,
  square: string,
  button: "left" | "right" = "left",
) {
  const board = page.locator(".board-frame").first();
  await boardVisible(page);
  const black = (await board.getAttribute("data-orientation")) === "black";
  const box = (await board.locator(".cg-wrap").boundingBox())!;
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  const x = box.x + (((black ? 7 - file : file) + 0.5) * box.width) / 8;
  const y = box.y + (((black ? rank : 7 - rank) + 0.5) * box.height) / 8;
  await page.mouse.move(x, y);
  await page.mouse.down({ button });
  // Chessground resolves the drawn square on its animation frame before release.
  if (button === "right")
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
  await page.mouse.up({ button });
}

const test = base.extend<{ disposableProduct: void }>({
  disposableProduct: [
    async ({ request }, use) => {
      const health = await request.get(`${api}/health`);
      const disposableHealth: unknown = health.ok() ? await health.json() : null;
      assertDisposableTarget(disposableHealth);
      // A layout test can activate a pack before this fixture runs. Stop its
      // admission before archiving cards, or the active-card quota replaces them.
      const catalog = await (await request.get(`${api}/tactics/catalog`)).json();
      const activePackIds = catalog.packs
        .filter((pack: { active: boolean }) => pack.active)
        .map((pack: { id: string }) => pack.id);
      if (activePackIds.length) {
        const deactivated = await request.put(`${api}/tactics/activation`, {
          data: { pack_ids: activePackIds, active: false },
        });
        expect(deactivated.ok(), await deactivated.text()).toBe(true);
        await expect.poll(async () => {
          const updatedCatalog = await (await request.get(`${api}/tactics/catalog`)).json();
          return updatedCatalog.packs.filter((pack: { active: boolean }) => pack.active);
        }).toEqual([]);
      }
      const repertoires = (
        await (await request.get(`${api}/repertoires`)).json()
      ).repertoires;
      for (const item of repertoires) {
        if (item.id.startsWith("__")) continue;
        const removal = await request.delete(`${api}/repertoires/${item.id}`);
        expect(removal.ok(), await removal.text()).toBe(true);
        if (removal.status() === 202) {
          const operationId = (await removal.json()).operation_id;
          await expect.poll(async () => (await (await request.get(`${api}/operations/${operationId}`)).json()).state,
            { timeout: 20_000 }).toBe("complete");
        }
      }
      // Each case starts a new study workspace. Exclusions persist throughout a
      // case's reload/restart proofs, then only this runner's database is reset.
      const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
      if (!project || !/^tempo-(?:pg-)?regressions-\d+-[a-f0-9]+$/.test(project))
        throw new Error("Product fixture reset requires its owning disposable runner");
      if (project.startsWith("tempo-pg-")) {
        await resetPostgresDeletedCards({ health: disposableHealth, apiUrl: api, project });
      } else {
        execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.test.yml", "exec", "-T", "api", "python", "-c",
          "import sqlite3; db=sqlite3.connect('/data/tempo.db'); db.execute('DELETE FROM deleted_cards'); db.commit(); db.close()"],
        { encoding: "utf8", timeout: 30_000 });
      }
      const queue = (await (await request.get(`${api}/queue/today`)).json())
        .cards;
      for (const card of queue)
        if (card.content_type !== "opening")
          await request.delete(`${api}/cards/${card.id}`);
      const settings = await (await request.get(`${api}/settings`)).json();
      await request.put(`${api}/settings`, {
        data: {
          ...settings,
          new_cards_per_day: 2,
          initial_depth: 2,
          lichess_username: "",
          chesscom_username: "",
        },
      });
      await use();
    },
    { auto: true },
  ],
});

export {
  test,
  expect,
  Chess,
  api,
  pgn,
  blackPgn,
  nav,
  boardVisible,
  assertSharedBoardShellLayout,
  move,
  clickSquare,
};
