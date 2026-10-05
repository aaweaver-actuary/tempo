import { Chess } from "chess.js";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate } from "./ui-fixtures";
import { expectedPieces, renderedPieces, playMove, squareCenter } from "./keyboard-fixtures";

test.use({ serviceWorkers: "block" });

test("incorrect study answers retain their live board while Home End and R browse the revealed reference", async ({ page }) => {
  await prepareVisualUI(page);
  const startingFen = new Chess().fen();
  const submitted = new Chess(); submitted.move("d4"); submitted.move("d5");
  const reference = new Chess(); reference.move("e4"); reference.move("e5");
  await page.route("**/api/queue/window?**", route => route.fulfill({ json: { count: 1, cards: [{
    id: "study-keyboard", queue_entry_id: 901, start_fen: startingFen, moves: [], content_type: "study_exercise",
    repertoire_name: "Keyboard study", repertoire_source: "Study", study_id: "keyboard-study",
    study_exercise_id: "keyboard-exercise", revision: 1, trained_color: "white",
  }] } }));
  await page.route("**/api/studies/keyboard-study/exercises/keyboard-exercise/present", route => route.fulfill({ json: {
    id: "keyboard-exercise", revision: 1, type: "move_line", prompt: "Find the reference line", hint: "Develop a piece", fen: startingFen,
  } }));
  const submissions: Record<string, unknown>[] = [];
  await page.route("**/api/studies/keyboard-study/exercises/keyboard-exercise/attempts", route => {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ json: { attempt_id: "study-answer", assessment: { outcome: "incorrect", feedback: "Review the reference" }, rating: "again" } });
  });
  await page.route("**/api/studies/keyboard-study/exercises/keyboard-exercise/attempts/*/feedback", route => route.fulfill({ json: {
    specification: { type: "move_line", prompt: "Find the reference line", hint: "Develop a piece", explanation: "Reference continuation",
      grading_policy: "reference", mode: "stepwise_line", accepted_lines: [["e2e4", "e7e5"]] },
  } }));
  await page.reload();
  await expect(page.getByText("Find the reference line")).toBeVisible();
  const board = page.locator(".board-frame");
  await playMove(page, board, "d2", "d4");
  await expect(board).toHaveAttribute("data-fen", (() => { const position = new Chess(); position.move("d4"); return position.fen(); })());
  await playMove(page, board, "d7", "d5");
  await expect(board).toHaveAttribute("data-fen", submitted.fen());
  await page.getByRole("button", { name: "Hint", exact: true }).click();
  await page.getByRole("button", { name: "Submit", exact: true }).click();
  await expect(page.getByText("Reference continuation")).toBeVisible();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(submitted.fen()));
  await page.keyboard.press("Home");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(startingFen));
  await expect(board).toHaveAttribute("data-input-enabled", "false");
  await page.keyboard.press("End");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(reference.fen()));
  await expect(board).toHaveAttribute("data-input-enabled", "false");
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(reference.fen()));
  await page.keyboard.press("r");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(submitted.fen()));
  await expect(page.getByText("Answer: d2d4 d7d5")).toBeVisible();
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ answer: { type: "move_line", moves: ["d2d4", "d7d5"] }, hint_seen: true });
});

test("training arrows never uncover the next answer and R restores the decision without grading", async ({ page }) => {
  await prepareVisualUI(page);
  const writes: string[] = [];
  page.on("request", request => { if (request.method() === "POST" && /\/review$/.test(request.url())) writes.push(request.url()); });
  const board = page.locator(".board-frame");
  const root = new Chess().fen();
  await expect(board).toHaveAttribute("data-fen", root);
  await playMove(page, board, "e2", "e4");
  const decision = new Chess(); decision.move("e4"); decision.move("e5");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("End");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  await page.keyboard.press("Home");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(root));
  await expect(board).toHaveAttribute("data-input-enabled", "false");
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "black");
  await page.keyboard.press("r");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  expect(writes).toEqual([]);
});

test("Builder shortcuts cancel a held piece and preserve notes, selection and splitter keys", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  // The line worker publishes the initial repertoire after navigation. That
  // changes the board's position identity and intentionally cancels old input;
  // start the keyboard drag only after the intended Builder scope is ready.
  await expect(page.getByRole("combobox", { name: "Active repertoire" })).toHaveValue("visual-repertoire");
  const board = page.locator(".board-frame");
  await expect(page.locator(".persistent-board-shell")).toHaveAttribute("data-board-owner", "builder");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const root = new Chess().fen();
  await board.scrollIntoViewIfNeeded();
  const from = await squareCenter(board, "e2"), to = await squareCenter(board, "e4");
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await expect(board.locator("piece.dragging")).toHaveCount(1);
  await page.keyboard.press("r");
  await expect(board.locator("piece.dragging")).toHaveCount(0);
  await page.mouse.up();
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(root));
  await playMove(page, board, "e2", "e4");
  const decision = new Chess(); decision.move("e4");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  await page.keyboard.press("ArrowUp"); await page.keyboard.press("r");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision.fen()));
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  const note = page.getByRole("textbox", { name: "Position comment" });
  await note.fill("Keep this draft"); await note.press("f"); await note.press("ArrowLeft");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
  const splitter = page.getByRole("separator", { name: "Board size" });
  await splitter.focus(); await splitter.press("ArrowRight");
  await expect(board).toHaveAttribute("data-fen", decision.fen());
});

test("Settings letter preference takes effect immediately and persists while arrows and help work", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Settings");
  await page.getByRole("tab", { name: "Board", exact: true }).click();
  const preference = page.getByRole("checkbox", { name: /Letter keyboard shortcuts/ });
  await preference.uncheck();
  await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  await playMove(page, board, "e2", "e4");
  const playedPosition = new Chess(); playedPosition.move("e4");
  await expect(board).toHaveAttribute("data-fen", playedPosition.fen());
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(playedPosition.fen()));
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "white");
  await page.keyboard.press("Home"); await expect(board).toHaveAttribute("data-fen", new Chess().fen());
  await page.locator(".board-viewport").focus(); await page.keyboard.press("?");
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toBeVisible(); await expect(help).toContainText("Flip board (unavailable)");
  await page.keyboard.press("Escape"); await expect(help).toHaveCount(0);
  await page.reload(); await navigate(page, "Settings");
  await page.getByRole("tab", { name: "Board", exact: true }).click(); await expect(preference).not.toBeChecked();
  await preference.check(); await navigate(page, "Builder");
  await page.keyboard.press("f"); await expect(page.locator(".board-frame")).toHaveAttribute("data-orientation", "black");
});

test("Escape closes Builder search and header popups one at a time and restores their openers", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
  const search = page.getByRole("button", { name: /Position search/ });
  await search.click(); await expect(page.getByRole("dialog", { name: "Position search" })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(search).toBeFocused();
  const local = page.getByRole("button", { name: "Open local data menu" });
  const notifications = page.getByRole("button", { name: "Notifications" });
  await local.click(); await notifications.click();
  await page.keyboard.press("Escape");
  await expect(notifications).toBeFocused(); await expect(page.locator("#local-data-menu")).toBeVisible();
  await page.keyboard.press("Escape"); await expect(page.locator("#local-data-menu")).toHaveCount(0);
  await expect(local).toBeFocused();
  const activity = page.getByRole("button", { name: "Analysis activity" });
  await activity.click(); await page.keyboard.press("Escape"); await expect(activity).toBeFocused();
  await expect(page.locator("#tempo-activity-content")).toHaveCount(0);
});
