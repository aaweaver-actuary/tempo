import type { APIRequestContext, APIResponse } from "@playwright/test";
import { api, test, expect, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";

async function confirmed(request: APIRequestContext, response: APIResponse) {
  const body = await response.json();
  expect(response.ok(), JSON.stringify(body)).toBeTruthy();
  if (response.status() !== 202) return body;
  let receipt: { state: string; response?: unknown };
  await expect.poll(async () => {
    receipt = await (await request.get(`${api}/operations/${body.operation_id}`)).json();
    return receipt.state;
  }, { timeout: 30_000 }).toBe("complete");
  return receipt!.response as typeof body;
}

for (const width of [390, 1280]) {
  test(`one real short-prefix practice admits its child while parent is learning ${width}`, async ({ page, request }) => {
    const imported = await confirmed(request, await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: `progression-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
        '[Event "Progression"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *') },
      trained_color: "white", initial_depth: "2",
    } }));
    const repertoireId = imported.repertoire_id as string;
    await confirmed(request, await request.put(`${api}/repertoires/${repertoireId}/settings`, { data: { new_cards_per_day: 2 } }));
    const queue = async () => (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards as
      { id: string; moves: string[]; repertoire_id?: string }[];
    let rootId = "";
    await expect.poll(async () => {
      const cards = await queue();
      rootId = cards.find(card => card.moves.join(",") === "e2e4,e7e5,g1f3")?.id ?? "";
      return rootId.length > 0;
    }, { timeout: 30_000 }).toBe(true);
    expect((await queue()).some(card => card.moves.join(",") === "b8c6,f1b5")).toBe(false);
    await page.setViewportSize({ width, height: 844 });
    await prepareUI(page);
    const reviewSent = page.waitForRequest(request => request.url().includes(`/cards/${rootId}/review`) && request.method() === "POST");
    await move(page, "e2", "e4");
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4p3\/4P3/);
    await move(page, "g1", "f3");
    await reviewSent;
    await expect.poll(async () => (await queue()).some(card => card.moves.join(",") === "b8c6,f1b5"),
      { timeout: 30_000, message: "A saved practice opens the immediate child without a maturity wait" }).toBe(true);
    const summary = await (await request.get(`${api}/repertoires/${repertoireId}/statistics?window=all`)).json();
    expect(summary.cards.mature).toBe(0);
    expect(summary.cards.learning).toBe(2);
    expect(summary.cards.locked).toBe(1);
    expect((await queue()).some(card => card.moves.join(",") === "a7a6,b5a4")).toBe(false);
  });
}
