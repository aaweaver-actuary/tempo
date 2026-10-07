<<<<<<< HEAD
import { test, expect } from "./observability";
=======
import { test, expect } from "./product-fixtures";
>>>>>>> main
import { assertDisposableTarget } from "./disposable-target";
import { navigate } from "./ui-fixtures";

const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
  ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");
const pgn = '[Event "Synthetic first"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 *\n\n[Event "Synthetic second"]\n\n1. e4 c5 2. Nf3 d6 3. d4 *';

test("repertoire limits update today's queue, persist after reload, and reset to default", async ({ page, request }) => {
  assertDisposableTarget(await (await request.get(`${api}/health`)).json());
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  let repertoireId = "";
  try {
    const changed = await request.put(`${api}/settings`, { data: { ...originalSettings, new_cards_per_day: 2 } });
    expect(changed.ok()).toBe(true);
    await expect.poll(async () => (await (await request.get(`${api}/settings`)).json()).new_cards_per_day).toBe(2);
    const imported = await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: "override-fixture.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from(pgn) },
      trained_color: "white", initial_depth: "2",
    } });
    expect(imported.ok()).toBe(true);
    repertoireId = (await imported.json()).repertoire_id;
    await expect.poll(async () => (await (await request.get(`${api}/repertoires/${repertoireId}/integrity`)).json()).status, { timeout: 20_000 }).toBe("clean");
    const repertoire = (await (await request.get(`${api}/repertoires`)).json()).repertoires.find((item: { id: string }) => item.id === repertoireId);
    const queueCount = async () => (await (await request.get(`${api}/queue/today`)).json()).cards.filter((card: { repertoire_id: string }) => card.repertoire_id === repertoireId).length;
    await expect.poll(queueCount, { timeout: 20_000 }).toBe(2);
    await page.goto("/"); await navigate(page, "Settings");
    const row = page.getByRole("group", { name: `${repertoire.name} daily limit`, exact: true });
    await expect(row).toContainText("Current limit: 2/day");
    await row.getByLabel(`${repertoire.name} allowance`).selectOption("custom");
    await row.getByLabel(`${repertoire.name} new cards per day`).fill("1");
    // Failure must remain visible and retry must resolve the same durable save.
    await page.route("**/api/repertoires/*/settings", route => route.fulfill({ status: 503, json: { detail: "Settings worker unavailable" } }));
    await row.getByRole("button", { name: `Save limit for ${repertoire.name}` }).click();
    await expect(row.getByRole("alert")).toContainText("Settings worker unavailable");
    await page.unroute("**/api/repertoires/*/settings");
    await row.getByRole("button", { name: `Check pending save for ${repertoire.name}` }).click();
    const checkSave = row.getByRole("button", { name: `Check pending save for ${repertoire.name}` });
    await expect.poll(async () => {
      if (await checkSave.isVisible() && await checkSave.isEnabled()) await checkSave.click();
      return await row.textContent();
    }, { timeout: 20_000 }).toContain("Current limit: 1/day");
    await expect.poll(queueCount, { timeout: 20_000 }).toBe(1);
    await page.reload(); await navigate(page, "Settings");
    await expect(row.getByLabel(`${repertoire.name} new cards per day`)).toHaveValue("1");
    await row.getByLabel(`${repertoire.name} allowance`).selectOption("default");
    await row.getByRole("button", { name: `Save limit for ${repertoire.name}` }).click();
    await expect.poll(async () => {
      if (await checkSave.isVisible() && await checkSave.isEnabled()) await checkSave.click();
      return await row.textContent();
    }, { timeout: 20_000 }).toContain("Current limit: 2/day");
    await expect.poll(queueCount, { timeout: 20_000 }).toBe(2);
  } finally {
    if (repertoireId) await request.delete(`${api}/repertoires/${repertoireId}`);
    await request.put(`${api}/settings`, { data: originalSettings });
  }
});
