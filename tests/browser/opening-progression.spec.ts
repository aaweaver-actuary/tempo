import { execFileSync } from "node:child_process";
import type { APIRequestContext, APIResponse } from "@playwright/test";
import { api, test, expect, move } from "./product-fixtures";
import { prepareUI } from "./ui-fixtures";
import { verifyPostgresFixtureTarget, postgresFixtureSqlArguments } from "./product-fixture-reset";

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
  test(`one real short-prefix practice admits its child while parent is learning ${width}`, async ({ page, request }, testInfo) => {
    const imported = await confirmed(request, await request.post(`${api}/imports/pgn`, { multipart: {
      file: { name: `progression-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
        '[Event "Progression"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *') },
      trained_color: "white", initial_depth: "2",
    } }));
    const repertoireId = imported.repertoire_id as string;
    await confirmed(request, await request.put(`${api}/repertoires/${repertoireId}/settings`, { data: { new_cards_per_day: 2 } }));
    const queue = async () => {
      let response = await request.get(`${api}/queue/window?limit=20`);
      if (response.status() === 202) {
        const acknowledgment = await response.json();
        expect(acknowledgment.operation_id).toMatch(/^queue\.issue_head:/);
        expect(["queued", "executing", "retrying", "pending"]).toContain(acknowledgment.state);
        // Issuance is an asynchronous command, not a queue-shaped response.
        // Settle that exact command before rereading authoritative queue facts.
        await expect.poll(async () => {
          const receiptResponse = await request.get(`${api}/operations/${encodeURIComponent(acknowledgment.operation_id)}`);
          expect(receiptResponse.ok(), await receiptResponse.text()).toBe(true);
          const receipt = await receiptResponse.json();
          expect(receipt.operation_id).toBe(acknowledgment.operation_id);
          expect(["queued", "executing", "retrying", "pending", "complete"]).toContain(receipt.state);
          return receipt.state;
        }, { timeout: 30_000 }).toBe("complete");
        response = await request.get(`${api}/queue/window?limit=20`);
      }
      expect(response.status(), await response.text()).toBe(200);
      const body = await response.json();
      expect(Array.isArray(body.cards)).toBe(true);
      return body.cards as { id: string; queue_entry_id: number; moves: string[]; repertoire_id?: string }[];
    };
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
    const firstSaveStarted = performance.now();
    await move(page, "g1", "f3");
    const submitted = await reviewSent;
    const operationId = submitted.headers()["idempotency-key"];
    expect(operationId).toMatch(/^review-attempt:/);
    let durableReceipt: Record<string, unknown> = {};
    await expect.poll(async () => {
      durableReceipt = await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json();
      return durableReceipt;
    }, { timeout: 30_000 }).toMatchObject({ operation_id: operationId, state: "complete", response: { persisted: true } });
    await expect.poll(async () => (await queue()).some(card => card.moves.join(",") === "b8c6,f1b5"),
      { timeout: 30_000, message: "A saved practice opens the immediate child without a maturity wait" }).toBe(true);
    // The statistics API was retired; inspect the same persisted maturity facts
    // read-only, on the verified disposable project, without reviving that API.
    const healthResponse = await request.get(`${api}/health`);
    expect(healthResponse.ok()).toBe(true);
    const postgresId = await verifyPostgresFixtureTarget({ health: await healthResponse.json(),
      apiUrl: api, project: process.env.TEMPO_TEST_COMPOSE_PROJECT });
    expect(repertoireId).toMatch(/^[a-zA-Z0-9_-]+$/);
    expect(rootId).toMatch(/^[a-zA-Z0-9_-]+$/);
    const maturitySql = `BEGIN READ ONLY;
      SELECT json_build_object(
        'mature', count(*) FILTER (WHERE card.state='mature'),
        'learning', count(*) FILTER (WHERE card.state='learning'),
        'locked', count(*) FILTER (WHERE card.state='locked'),
        'parent_state', max(card.state) FILTER (WHERE card.id='${rootId}'))
      FROM cards card WHERE card.content_type='opening' AND card.archived=0
        AND card.superseded_by IS NULL
        AND (card.repertoire_id='${repertoireId}' OR EXISTS (
          SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id
            AND link.repertoire_id='${repertoireId}'));
      COMMIT;`;
    const maturity = JSON.parse(execFileSync("docker", [...postgresFixtureSqlArguments(postgresId, maturitySql, 5000), "-q"],
      { encoding: "utf8", timeout: 10_000 }));
    expect(maturity).toEqual({ mature: 0, learning: 2, locked: 1, parent_state: "learning" });
    expect((await queue()).some(card => card.moves.join(",") === "a7a6,b5a4")).toBe(false);
    const child = (await queue()).find(card => card.moves.join(",") === "b8c6,f1b5")!;
    const firstResult = durableReceipt.response as { queue_entry_id: number; requeue_entry_id: number };
    expect(firstResult.queue_entry_id).toBe(submitted.postDataJSON().queue_entry_id);
    expect(firstResult.requeue_entry_id).not.toBe(firstResult.queue_entry_id);
    const firstNext = (await queue())[0];
    // First exposure is teaching-assisted. Its real receipt schedules a new
    // root attempt before the admitted child; admission does not mean head order.
    expect(firstNext.queue_entry_id).toBe(firstResult.requeue_entry_id);
    expect(firstNext.id).toBe(rootId);
    expect(child.queue_entry_id).not.toBe(firstNext.queue_entry_id);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(firstNext.queue_entry_id));
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    await move(page, "e2", "e4");
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4p3\/4P3/);
    const firstPlayableMs = performance.now() - firstSaveStarted;

    // Finish that actual second practice, without forcing a grade or changing
    // the queue. Require a second saved-review -> authoritative playable entry.
    const secondReviewSent = page.waitForRequest(requestEvent =>
      requestEvent.url().includes(`/cards/${rootId}/review`) && requestEvent.method() === "POST");
    const secondSaveStarted = performance.now();
    await move(page, "g1", "f3");
    const secondSubmitted = await secondReviewSent;
    expect(secondSubmitted.postDataJSON().queue_entry_id).toBe(firstNext.queue_entry_id);
    const secondOperationId = secondSubmitted.headers()["idempotency-key"];
    expect(secondOperationId).toMatch(/^review-attempt:/);
    expect(secondOperationId).not.toBe(operationId);
    let secondReceipt: Record<string, unknown> = {};
    await expect.poll(async () => {
      secondReceipt = await (await request.get(`${api}/operations/${encodeURIComponent(secondOperationId)}`)).json();
      return secondReceipt;
    }, { timeout: 30_000 }).toMatchObject({ operation_id: secondOperationId, state: "complete",
      response: { persisted: true, queue_entry_id: firstNext.queue_entry_id } });
    const secondNext = (await queue())[0];
    expect(secondNext).toBeDefined();
    expect(secondNext.queue_entry_id).not.toBe(firstNext.queue_entry_id);
    expect(secondNext.queue_entry_id).not.toBe(firstResult.queue_entry_id);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(secondNext.queue_entry_id));
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    // Either the issued root repeat or its admitted child can be authoritative;
    // both must expose their real legal first learner move, not a forged choice.
    expect([rootId, child.id]).toContain(secondNext.id);
    if (secondNext.id === rootId) {
      await move(page, "e2", "e4");
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4p3\/4P3/);
    } else {
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /2n5/);
      await move(page, "f1", "b5");
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /1B2p3/);
    }
    await testInfo.attach("opening-successive-playable-transitions", { contentType: "application/json",
      body: Buffer.from(JSON.stringify({ operationId, secondOperationId,
        entries: [firstResult.queue_entry_id, firstNext.queue_entry_id, secondNext.queue_entry_id],
        admittedChild: child.queue_entry_id, firstPlayableMs,
        secondPlayableMs: performance.now() - secondSaveStarted,
        timingNote: "First interval includes retained maturity SQL and child-admission checks; existing bounded polls/assertions unchanged." })) });
    expect(await (await request.get(`${api}/operations/${encodeURIComponent(secondOperationId)}`)).json()).toEqual(secondReceipt);
    expect(await (await request.get(`${api}/operations/${encodeURIComponent(operationId)}`)).json()).toEqual(durableReceipt);
  });
}
