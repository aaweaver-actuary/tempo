import assert from "node:assert/strict";

async function point(page, square) {
  const board = await page.locator("cg-board").boundingBox(); assert(board);
  const black = await page.locator(".tempo-board").getAttribute("data-orientation") === "black";
  const file = square.charCodeAt(0) - 97; const rank = Number(square[1]) - 1;
  return { x: board.x + (black ? 7.5 - file : file + 0.5) * board.width / 8,
    y: board.y + (black ? rank + 0.5 : 7.5 - rank) * board.height / 8 };
}
async function hold(page, square) {
  const from = await point(page, square);
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  await page.mouse.move(from.x + 12, from.y - 12, { steps: 3 });
  await page.locator("cg-board piece.dragging").waitFor({ state: "visible" });
}
async function drop(page, square) {
  const to = await point(page, square); await page.mouse.move(to.x, to.y, { steps: 6 }); await page.mouse.up();
}
async function action(page, id) { await page.locator(`#${id}`).evaluate(element => element.click()); }
async function moves(page) { return JSON.parse(await page.locator("#moves").innerText()); }
async function pawnAt(page, square) {
  const center = await point(page, square);
  await page.waitForFunction(target => [...document.querySelectorAll("cg-board piece.pawn")].some(piece => {
    const bounds = piece.getBoundingClientRect();
    return !piece.classList.contains("ghost") && Math.abs(bounds.x + bounds.width / 2 - target.x) < 1 &&
      Math.abs(bounds.y + bounds.height / 2 - target.y) < 1;
  }), center);
  return true;
}

export const scenarios = [
  { id: "BUI-N01", run: async page => {
    await hold(page, "e2");
    for (let index = 0; index < 20; index++) await action(page, "status-update");
    await page.waitForFunction(() => document.querySelector("#status").textContent === "20");
    await action(page, "annotations"); await action(page, "translate");
    await page.waitForFunction(() => document.querySelector("#heading p"));
    assert.equal(await page.locator("piece.dragging").count(), 1);
    await drop(page, "e4");
    await page.waitForFunction(() => JSON.parse(document.querySelector("#moves").textContent).length === 1);
    assert.deepEqual(await moves(page), [{ from: "e2", to: "e4", positionIdentity: "first", positionRevision: 0 }]);
    assert.equal(await page.locator("#handler-statuses").innerText(), "[20]");
    assert(await pawnAt(page, "e4"));
  } },
  { id: "BUI-N02", run: async page => {
    // Delay real Chessground's 1ms deferred callbacks; never manufacture board events.
    await page.evaluate(() => {
      const original = window.setTimeout.bind(window);
      window.pendingBoardCallbacks = [];
      window.setTimeout = (callback, delay, ...args) => {
        if (delay === 1 && typeof callback === "function") {
          window.pendingBoardCallbacks.push(() => callback(...args)); return original(() => {}, 0);
        }
        return original(callback, delay, ...args);
      };
    });
    await hold(page, "e2"); await drop(page, "e4");
    assert(await page.evaluate(() => window.pendingBoardCallbacks.length > 0));
    await action(page, "replace");
    await page.locator('.tempo-board[data-position-identity="replacement"]').waitFor();
    await page.evaluate(() => window.pendingBoardCallbacks.splice(0).forEach(callback => callback()));
    assert.deepEqual(await moves(page), []); assert(await pawnAt(page, "e2"));
  } },
  { id: "BUI-N03", run: async page => {
    for (const invalidation of ["replace", "revision", "lock", "flip", "mode-select"]) {
      await action(page, "reset"); await hold(page, "e2"); await action(page, invalidation); await drop(page, "e4");
      assert.deepEqual(await moves(page), [], invalidation); assert(await pawnAt(page, "e2"), invalidation);
    }
  } },
  { id: "BUI-N04", run: async page => {
    await action(page, "mode-free"); await hold(page, "a7"); await drop(page, "a4");
    await page.waitForFunction(() => JSON.parse(document.querySelector("#moves").textContent).length === 1);
    assert(await pawnAt(page, "a4"));
    await action(page, "mode-legal"); assert(await pawnAt(page, "a7"));
    await page.locator("#focus").click(); await page.keyboard.press("Escape");
    assert(await page.locator(".tempo-board").evaluate(element => document.activeElement === element));
    await page.keyboard.press("Tab");
    assert(!(await page.locator(".tempo-board").evaluate(element => document.activeElement === element)));
    await action(page, "remount"); assert.equal(await page.locator("cg-board").count(), 0);
    await action(page, "remount"); await hold(page, "e2"); await drop(page, "e4");
    await page.waitForFunction(() => JSON.parse(document.querySelector("#moves").textContent).length === 2);
  } },
  { id: "BUI-N05", deviceScaleFactor: 1.25, run: async page => {
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.waitForFunction(() => {
        const size = document.querySelector("cg-board").getBoundingClientRect().width;
        const available = document.querySelector("#board-container").getBoundingClientRect().width;
        return size > 0 && size === Math.floor(Math.min(available, 760) / 8) * 8;
      });
      const board = await page.locator("cg-board").boundingBox();
      const container = await page.locator("#board-container").boundingBox();
      assert(board.width <= container.width); assert.equal(board.width, board.height);
      assert.equal(board.width / 8, Math.floor(board.width / 8));
      assert(board.x >= 0 && board.x + board.width <= width);
    }
  } },
];
