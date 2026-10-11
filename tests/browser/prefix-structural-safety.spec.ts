import { test, expect, api, nav } from "./product-fixtures";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import { prefixTransitionPlanSchema, transitionRequest, transitionSummary } from "../../app/domain/prefix-transition";
import type { APIRequestContext, Page } from "@playwright/test";

async function prepareReservation(request: APIRequestContext, label: string) {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `${label}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
      '[Event "Advance"]\n\n1. e4 c6 2. d4 d5 3. e5 c5 *\n\n[Event "Knight"]\n\n1. e4 c6 2. d4 d5 3. Nf3 Bg4 *') },
    trained_color: "black", initial_depth: "3",
  } });
  expect(imported.ok(), await imported.text()).toBe(true);
  const repertoireId = (await imported.json()).repertoire_id as string;
  let source: ReturnType<typeof prefixSourceSchema.parse>;
  await expect.poll(async () => {
    const response = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
    if (!response.ok()) {
      expect(["evaluation_busy", "graph_not_ready"]).toContain((await response.json()).detail.code);
      return false;
    }
    source = prefixSourceSchema.parse(await response.json());
    return source.graph_generation > 0;
  }, { timeout: 30_000, message: "Authoritative source publication before browser reservation" }).toBe(true);
  const depths = Object.fromEntries(source!.lines.map(line => [line.id, 2]));
  const response = await request.post(`${api}/repertoires/${repertoireId}/prefix-transition/plan`, {
    data: { snapshot_id: source!.snapshot_id, selected_line_ids: source!.lines.map(line => line.id), candidate_depths: depths },
  });
  expect(response.ok(), await response.text()).toBe(true);
  const plan = prefixTransitionPlanSchema.parse(await response.json());
  expect(plan.status).toBe("ready");
  return { version: 1, operationId: crypto.randomUUID(), repertoireId,
    request: transitionRequest(plan, depths), summary: transitionSummary(plan, source!, label), state: "pending",
    approval: { plan, source: { repertoire_id: source!.repertoire_id, snapshot_id: source!.snapshot_id, graph_generation: source!.graph_generation, lines: source!.lines.filter(line => plan.selected_line_ids.includes(line.id)) } } };
}

async function openDeletion(page: Page, label: string) {
  await page.route("**/api/queue/**", async route => {
    await page.getByRole("heading", { name: "Repertoire", exact: true }).waitFor();
    await route.continue();
  });
  await page.goto("/"); await nav(page, "Repertoire");
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: label, exact: true }) });
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Delete", exact: true }).click();
  return page.getByRole("dialog", { name: new RegExp(`Delete “${label}”`) });
}

for (const writer of ["same-tab", "cross-tab"] as const) {
  test(`issue81_${writer}_already_open_deletion_dialog_suppresses_real_DELETE_before_server_admission`, async ({ page, context, request }) => {
    const label = `structural-${writer}`;
    const reservation = await prepareReservation(request, label);
    const dialog = await openDeletion(page, label);
    const confirm = dialog.getByRole("button", { name: "Delete repertoire", exact: true });
    await expect(confirm).toBeEnabled();
    const mutations: string[] = [];
    page.on("request", incoming => { if (incoming.method() === "DELETE" && incoming.url().includes(`/repertoires/${reservation.repertoireId}`)) mutations.push(incoming.url()); });
    const writerPage = writer === "same-tab" ? page : await context.newPage();
    if (writer === "cross-tab") {
      await writerPage.route("**/structural-storage-writer.html", route => route.fulfill({ contentType: "text/html", body: "<title>Structural storage fixture</title>" }));
      await writerPage.goto("/structural-storage-writer.html");
    }
    await writerPage.evaluate(async operation => {
      const key = `tempo-pending-prefix-transition-v1:${operation.repertoireId}`;
      await navigator.locks.request("tempo-pending-prefix-transition-v1:reservation", () => {
        localStorage.setItem(key, JSON.stringify(operation));
      });
      // Same-tab feature notifications use this event; the other tab receives native storage delivery.
      window.dispatchEvent(new StorageEvent("storage", { key }));
    }, reservation);
    await expect(confirm).toBeDisabled();
    await expect(dialog.getByRole("alert")).toContainText("Resolve the prefix transition");
    expect(mutations).toEqual([]);
    expect(await page.evaluate(() => localStorage.getItem("tempo-pending-repertoire-delete-v1"))).toBeNull();
    const existing = await request.get(`${api}/repertoires`);
    expect((await existing.json()).repertoires.some((entry: { id: string }) => entry.id === reservation.repertoireId)).toBe(true);
    if (writer === "cross-tab") await writerPage.close();
  });
}

test("issue81_delayed_notification_stale_confirmation_sends_no_real_DELETE", async ({ page, request }) => {
  const label = "structural-delayed";
  const reservation = await prepareReservation(request, label);
  const dialog = await openDeletion(page, label);
  await expect(dialog.getByRole("button", { name: "Delete repertoire", exact: true })).toBeEnabled();
  const mutations: string[] = [];
  page.on("request", incoming => { if (incoming.method() === "DELETE") mutations.push(incoming.url()); });
  // One browser task intentionally delays all notifications until after the stale
  // control is clicked. Final command admission must observe storage independently.
  const staleEnabled = await page.evaluate(async operation => {
    await navigator.locks.request("tempo-pending-prefix-transition-v1:reservation", () => {
      localStorage.setItem(`tempo-pending-prefix-transition-v1:${operation.repertoireId}`, JSON.stringify(operation));
    });
    const button = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(item => item.textContent === "Delete repertoire")!;
    const enabled = !button.disabled; button.click(); return enabled;
  }, reservation);
  expect(staleEnabled).toBe(true);
  await expect(dialog.getByRole("alert").filter({ hasText: "Resolve the prefix transition" }).first()).toBeVisible();
  expect(mutations).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-repertoire-delete-v1"))).toBeNull();
});

test("issue81_stale_canonical_confirmation_sends_no_real_PUT_after_reservation", async ({ page, request }) => {
  const label = "structural-canonical";
  const reservation = await prepareReservation(request, label);
  const deletion = await openDeletion(page, label);
  await deletion.getByRole("button", { name: "Cancel", exact: true }).click();
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: label, exact: true }) });
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Canonical prefix…", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /Canonical prefix/ });
  await expect(dialog.getByRole("button", { name: "Save prefix", exact: true })).toBeEnabled();
  const mutations: string[] = [];
  page.on("request", incoming => { if (incoming.method() === "PUT" && incoming.url().includes("canonical-prefix")) mutations.push(incoming.url()); });
  const staleEnabled = await page.evaluate(async operation => {
    await navigator.locks.request("tempo-pending-prefix-transition-v1:reservation", () => {
      localStorage.setItem(`tempo-pending-prefix-transition-v1:${operation.repertoireId}`, JSON.stringify(operation));
    });
    const button = [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(item => item.textContent === "Save prefix")!;
    const enabled = !button.disabled; button.click(); return enabled;
  }, reservation);
  expect(staleEnabled).toBe(true);
  await expect(dialog.getByRole("alert").filter({ hasText: "Resolve the prefix transition" }).first()).toBeVisible();
  expect(mutations).toEqual([]);
});
