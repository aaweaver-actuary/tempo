import { test, expect, api, nav } from "./product-fixtures";
import type { Page } from "@playwright/test";

test.use({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" });
test.beforeEach(async ({ context }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true, configurable: true }));
});

const fen = "4k3/8/8/8/8/8/8/4K1N1 w - - 0 1";
const originalPgn = `[Event "Original synthetic study"]\n[SetUp "1"]\n[FEN "${fen}"]\n\n*\n`;

async function expectStudyNoticeInHistory(page: Page, message: string) {
  await expect(page.locator(".notification-toast.notification-info, .notification-toast.notification-success")).toHaveCount(0);
  await page.getByRole("button", { name: "Notifications" }).click();
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.locator(".notification-list").getByText(message, { exact: true })).toBeVisible();
  await page.locator(".notification-tray").getByRole("button", { name: "Close", exact: true }).click();
}

async function waitForPreparedPhoneShell(page: Page, localDate: string): Promise<void> {
  await expect.poll(() => page.evaluate(async (expectedDate) => {
    const controller = navigator.serviceWorker.controller;
    if (!controller) return false;
    const shellReady = await new Promise<boolean>((resolve) => {
      const channel = new MessageChannel();
      const timeout = window.setTimeout(() => resolve(false), 1_000);
      channel.port1.onmessage = (event) => {
        window.clearTimeout(timeout);
        channel.port1.close();
        resolve(event.data?.ready === true);
      };
      controller.postMessage({ type: "tempo:offline-shell-status" }, [channel.port2]);
    });
    if (!shellReady) return false;
    const opened = indexedDB.open("tempo-offline-training", 2);
    const database = await new Promise<IDBDatabase | null>((resolve) => {
      opened.onsuccess = () => resolve(opened.result);
      opened.onerror = () => resolve(null);
    });
    if (!database) return false;
    const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    const prepared = await new Promise<{ localDate?: string; cards?: unknown[] } | null>((resolve) => {
      read.onsuccess = () => resolve(read.result ?? null);
      read.onerror = () => resolve(null);
    });
    database.close();
    return prepared?.localDate === expectedDate && Boolean(prepared.cards?.length);
  }, localDate)).toBe(true);
}

test("prepared study queue shares the disposable service calendar day", async ({ page, request }) => {
  await page.goto("/");
  const browserCalendar = await page.evaluate(() => {
    const now = new Date();
    return {
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      localDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    };
  });
  expect(browserCalendar.timezone).toBe("America/New_York");
  const prepared = await request.get(`${api}/queue/prepared`);
  expect(prepared.ok()).toBeTruthy();
  expect((await prepared.json()).local_date).toBe(browserCalendar.localDate);
});

test("FEN-only study square exercise is authored enrolled and reviewed through the real workspace", async ({ page, request }) => {
  // Compose's local queue day can differ from the browser after UTC midnight.
  // Pin this workflow to that day while its timers continue running normally.
  const serverQueue = await (await request.get(`${api}/queue/today`)).json();
  // A small smoke can reach this case before the initial durable queue slice
  // finishes. Establish its ready boundary before browser preparation starts.
  await expect.poll(async () => {
    const preparedQueue = await request.get(`${api}/queue/prepared`);
    expect(preparedQueue.ok()).toBeTruthy();
    return preparedQueue.json();
  }, { message: "Initial disposable queue is ready for phone preparation", timeout: 30_000 })
    .toMatchObject({ local_date: serverQueue.local_date, projection: { state: "ready" } });
  await page.clock.setFixedTime(new Date(`${serverQueue.local_date}T12:00:00Z`));
  const catalog = await (await request.get(`${api}/tactics/catalog`)).json();
  expect(catalog.packs.filter((pack: { active: boolean }) => pack.active)).toEqual([]);
  await page.goto("/");
  await nav(page, "Studies");
  await page.getByLabel("Title", { exact: true }).fill("Synthetic knight study");
  await page.getByRole("button", { name: "Create study" }).press("Enter");
  await expectStudyNoticeInHistory(page, "Study created");
  await page.getByLabel("New chapter").fill("Geometry");
  await page.getByRole("button", { name: "Add chapter" }).press("Enter");
  await page.getByLabel("Or paste PGN").fill(originalPgn);
  await page.getByRole("button", { name: "Preview PGN" }).press("Enter");
  await expect(page.getByText(/Record 1: Original synthetic study, 1 positions, valid/)).toBeVisible();
  await page.getByRole("button", { name: "Commit selected records" }).press("Enter");
  await expect(page.getByRole("button", { name: new RegExp(`Root position.*${fen}`) })).toBeVisible();
  await page.getByLabel("Question").fill("Select the white knight square");
  await page.getByLabel("Authored criterion").fill("The square occupied by the white knight");
  await page.getByLabel("Required squares").fill("g1");
  await page.getByRole("button", { name: "Create draft exercise" }).press("Enter");
  await expectStudyNoticeInHistory(page, "Draft exercise created; enroll it when ready");
  await page.getByRole("button", { name: /Select the white knight square.*draft/ }).click();
  await page.getByRole("button", { name: "Preview learner prompt" }).press("Enter");
  await expect(page.getByLabel("Learner preview")).toContainText("Select the white knight square");
  await page.getByRole("button", { name: "Enroll in daily queue" }).press("Enter");
  await expectStudyNoticeInHistory(page, "Exercise enrolled");
  await expect.poll(async () => {
    const queue = await (await request.get(`${api}/queue/today`)).json();
    return queue.cards.find((card: { content_type: string }) => card.content_type === "study_exercise")?.queue_entry_id;
  }).toBeGreaterThan(0);
  // Online study may start with an admitted card while refresh continues. The
  // complete offline phone copy additionally requires the published projection.
  await expect.poll(async () => {
    const prepared = await (await request.get(`${api}/queue/prepared`)).json();
    return prepared.projection.state === "ready" && prepared.count === prepared.cards.length &&
      prepared.cards.some((card: { content_type: string }) => card.content_type === "study_exercise");
  }).toBe(true);
  await nav(page, "Train");
  await expect(page.getByText("Select the white knight square")).toBeVisible();
  await expect(page.getByText("Original synthetic study")).toHaveCount(0);
  const browserLocalDate = await page.evaluate(() => {
    const today = new Date();
    return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  });
  await waitForPreparedPhoneShell(page, browserLocalDate);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", fen);
  const beforeFen = await page.locator(".board-frame").first().getAttribute("data-fen");
  await page.getByLabel("Coordinates or UCI move").fill("g1");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  expect(await page.locator(".board-frame").first().getAttribute("data-fen")).toBe(beforeFen);
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Square selection assessed" })).toBeVisible();
  const queue = await (await request.get(`${api}/queue/today`)).json();
  const card = queue.cards.find((item: { content_type: string }) => item.content_type === "study_exercise");
  expect(card?.study_exercise_id).toBeTruthy();
  const bundle = await (await request.get(`${api}/studies/${card.study_id}/export`)).json();
  expect(bundle.tables.study_positions).toHaveLength(1);
});

test("prepared study response is graded offline and replayed with its actual squares", async ({ page }) => {
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const studyCard = {
    id: "prepared-study-card", queue_entry_id: 901, cycle: 0, latest_review_id: 0, revision: 1,
    start_fen: fen, moves: [], content_type: "study_exercise", kind: "exercise",
    repertoire_id: null, repertoire_name: "Synthetic study", repertoire_source: "Study",
    study_id: "prepared-study", study_exercise_id: "prepared-exercise",
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "prepared-exercise", revision: 1,
      fen, specification: { type: "square_set", prompt: "Mark the knight square", hint: "", explanation: "The knight starts on g1.",
        criterion: "Square occupied by the white knight", required: ["g1"], optional: [], candidate_region: null } },
  };
  const queue = { local_date: localDate, count: 1, cards: [studyCard] };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: queue }));
  await page.route("**/api/queue/prepared?**", (route) => route.fulfill({ json: {
    ...queue, prepared_at: new Date().toISOString(), projection: { state: "ready", generation: 1,
      updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 },
  } }));
  await page.goto("/");
  await expect(page.getByText("Mark the knight square")).toBeVisible();
  await waitForPreparedPhoneShell(page, localDate);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Mark the knight square")).toBeVisible();
  await page.getByLabel("Coordinates or UCI move").fill("g1");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("The knight starts on g1.")).toBeVisible();
  const journal = await page.evaluate(async () => {
    const request = indexedDB.open("tempo-offline-training", 2);
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    return await new Promise<{ attempts: Array<{ answer: unknown }> }>((resolve, reject) => {
      read.onsuccess = () => resolve(read.result); read.onerror = () => reject(read.error);
    });
  });
  expect(journal.attempts[0].answer).toEqual({ type: "square_set", squares: ["g1"] });
  const replayed: unknown[] = [];
  await page.unroute("**/api/**");
  await page.route("**/api/studies/prepared-study/exercises/prepared-exercise/attempts", (route) => {
    replayed.push(route.request().postDataJSON());
    return route.fulfill({ json: { attempt_id: "saved", rating: "correct",
      assessment: { outcome: "correct", feedback: "Saved" },
      review: { review_id: 903, requeue_entry_id: 904 } } });
  });
  await page.reload();
  await expect.poll(() => replayed.length).toBe(1);
  expect(replayed[0]).toMatchObject({ answer: { type: "square_set", squares: ["g1"] },
    revision: 1, queue_entry_id: 901 });
});

test("all five study exercise types can be authored from the workspace", async ({ page, request }) => {
  const study = await (await request.post(`${api}/studies`, { data: { title: "Original exercise forms" } })).json();
  const chapter = await (await request.post(`${api}/studies/${study.id}/chapters`, { data: { title: "Forms" } })).json();
  const preview = await (await request.post(`${api}/studies/${study.id}/import/preview`, {
    data: { chapter_id: chapter.id, raw_pgn: originalPgn },
  })).json();
  expect((await request.post(`${api}/studies/${study.id}/import/commit`, {
    data: { chapter_id: chapter.id, raw_pgn: originalPgn, preview_digest: preview.digest, selected_records: [0] },
  })).ok()).toBeTruthy();
  await page.goto("/");
  await nav(page, "Studies");
  await page.getByRole("combobox", { name: "Study" }).selectOption(study.id);
  await expect(page.getByRole("button", { name: new RegExp(`Root position.*${fen}`) })).toBeVisible();

  await page.getByLabel("Interaction").selectOption("move_line");
  await page.getByLabel("Question").fill("Develop the knight");
  await page.getByLabel("Accepted UCI lines; one per row").fill("g1f3");
  await page.getByRole("button", { name: "Create draft exercise" }).press("Enter");
  await expect(page.getByRole("button", { name: /Develop the knight · draft/ })).toBeVisible();

  await page.getByLabel("Interaction").selectOption("knight_path");
  await page.getByLabel("Question").fill("Attack c1 with the knight");
  await page.getByLabel("Starting knight square").fill("g1");
  await page.getByLabel("Squares attacked by final knight").fill("c1");
  await page.getByLabel("Maximum hops").fill("1");
  await page.getByRole("button", { name: "Create draft exercise" }).press("Enter");
  await expect(page.getByRole("button", { name: /Attack c1 with the knight · draft/ })).toBeVisible();

  await page.getByLabel("Interaction").selectOption("choice");
  await page.getByLabel("Question").fill("Is the knight on g1?");
  await page.getByLabel("Options, one ID|text per row").fill("yes|Yes\nno|No");
  await page.getByLabel("Correct option IDs").fill("yes");
  await page.getByRole("button", { name: "Create draft exercise" }).press("Enter");
  await expect(page.getByRole("button", { name: /Is the knight on g1\? · draft/ })).toBeVisible();

  await page.getByLabel("Interaction").selectOption("explanation");
  await page.getByLabel("Question").fill("Explain the knight placement");
  await page.getByLabel("Rubric shown after response").fill("It starts on g1.");
  await page.getByRole("button", { name: "Create draft exercise" }).press("Enter");
  await expect(page.getByRole("button", { name: /Explain the knight placement · draft/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.getByRole("button", { name: "Learn", exact: true })).toBeVisible();
});

test("stale study revisions retain the phone answer as a replay conflict", async ({ page }) => {
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const card = {
    id: "stale-card", queue_entry_id: 951, cycle: 0, latest_review_id: 0, revision: 1,
    start_fen: fen, moves: [], content_type: "study_exercise", kind: "exercise",
    repertoire_id: null, repertoire_name: "Synthetic study", repertoire_source: "Study",
    study_id: "stale-study", study_exercise_id: "stale-exercise",
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "stale-exercise", revision: 1,
      fen, specification: { type: "square_set", prompt: "Mark the knight square", hint: "", explanation: "Knight on g1.",
        criterion: "White knight", required: ["g1"], optional: [], candidate_region: null } },
  };
  const queue = { local_date: localDate, count: 1, cards: [card] };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: queue }));
  await page.route("**/api/queue/prepared?**", (route) => route.fulfill({ json: { ...queue,
    prepared_at: new Date().toISOString(), projection: { state: "ready", generation: 1,
      updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } } }));
  await page.goto("/");
  await expect(page.getByText("Mark the knight square")).toBeVisible();
  await waitForPreparedPhoneShell(page, localDate);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await page.getByLabel("Coordinates or UCI move").fill("g1");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("Knight on g1.")).toBeVisible();
  await page.unroute("**/api/**");
  await page.route("**/api/studies/stale-study/exercises/stale-exercise/attempts", (route) =>
    route.fulfill({ status: 409, json: { detail: "Exercise revision changed; reload before answering" } }));
  await page.reload();
  await expect.poll(async () => page.evaluate(async () => {
    const request = indexedDB.open("tempo-offline-training", 2);
    const database = await new Promise<IDBDatabase>((resolve) => { request.onsuccess = () => resolve(request.result); });
    const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    return new Promise<{ answer: unknown; conflict: string }>((resolve) => { read.onsuccess = () => resolve(read.result.attempts[0]); });
  })).toMatchObject({ answer: { type: "square_set", squares: ["g1"] },
    conflict: "Exercise revision changed; reload before answering" });
});

test("unknown prepared study grader versions are unavailable offline", async ({ page }) => {
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const card = { id: "future-card", queue_entry_id: 961, cycle: 0, latest_review_id: 0, revision: 1,
    start_fen: fen, moves: [], content_type: "study_exercise", kind: "exercise",
    repertoire_id: null, repertoire_name: "Synthetic study", repertoire_source: "Study",
    study_id: "future-study", study_exercise_id: "future-exercise",
    study_snapshot: { schema_version: 1, grader_version: 99, exercise_id: "future-exercise", revision: 1,
      fen, specification: { type: "square_set", prompt: "Unavailable future question", hint: "", explanation: "", criterion: "Knight",
        required: ["g1"], optional: [], candidate_region: null } } };
  const queue = { local_date: localDate, count: 1, cards: [card] };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: queue }));
  await page.route("**/api/queue/prepared?**", (route) => route.fulfill({ json: { ...queue,
    prepared_at: new Date().toISOString(), projection: { state: "ready", generation: 1,
      updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } } }));
  await page.goto("/");
  await waitForPreparedPhoneShell(page, localDate);
  await expect.poll(async () => page.evaluate(async () => {
    const request = indexedDB.open("tempo-offline-training", 2);
    const database = await new Promise<IDBDatabase>((resolve) => { request.onsuccess = () => resolve(request.result); });
    const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    return new Promise<number>((resolve) => { read.onsuccess = () => resolve(read.result?.cards?.length ?? 0); });
  })).toBe(1);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByText("Unavailable future question")).toHaveCount(0);
  await expect(page.getByRole("main").getByText(/1 exercise requires the computer/)).toBeVisible();
});

test.describe("online study feedback recovery", () => {
  // Network fault injection must observe requests rather than service-worker fetches.
  test.use({ serviceWorkers: "block" });

  test("saved study attempt can retry feedback without a duplicate review", async ({ page, request }) => {
    const study = await (await request.post(`${api}/studies`, { data: { title: "Feedback recovery" } })).json();
    const chapter = await (await request.post(`${api}/studies/${study.id}/chapters`, { data: { title: "Recovery" } })).json();
    const preview = await (await request.post(`${api}/studies/${study.id}/import/preview`, {
      data: { chapter_id: chapter.id, raw_pgn: originalPgn },
    })).json();
    await request.post(`${api}/studies/${study.id}/import/commit`, {
      data: { chapter_id: chapter.id, raw_pgn: originalPgn, preview_digest: preview.digest, selected_records: [0] },
    });
    const chapterContent = await (await request.get(`${api}/studies/${study.id}/chapters/${chapter.id}`)).json();
    const created = await request.post(`${api}/studies/${study.id}/exercises`, { data: {
      position_id: chapterContent.positions[0].id,
      specification: { type: "choice", prompt: "Is the white knight on g1?", hint: "", explanation: "The knight starts on g1.",
        options: [{ id: "yes", text: "Yes" }, { id: "no", text: "No" }], correct_option_ids: ["yes"] },
    } });
    expect(created.ok()).toBeTruthy();
    await page.goto("/");
    await nav(page, "Studies");
    await page.getByRole("combobox", { name: "Study" }).selectOption(study.id);
    await page.getByRole("button", { name: /Is the white knight on g1\? · draft/ }).click();
    await page.getByRole("button", { name: "Practice without scheduling" }).click();
    let feedbackCalls = 0;
    await page.route("**/api/studies/*/exercises/*/attempts/*/feedback", (route) => {
      feedbackCalls += 1;
      return feedbackCalls === 1 ? route.fulfill({ status: 503, json: { detail: "Temporary outage" } }) : route.continue();
    });
    await page.getByLabel("Yes", { exact: true }).check();
    await page.getByRole("button", { name: "Submit" }).click();
    await expect(page.getByRole("button", { name: "Retry feedback" })).toBeVisible();
    await page.getByRole("button", { name: "Retry feedback" }).click();
    await expect(page.getByText("The knight starts on g1.")).toBeVisible();
    expect(feedbackCalls).toBe(2);
    const summary = await (await request.get(`${api}/studies/${study.id}/summary`)).json();
    expect(summary.chapters[0].attempts).toBe(1);
  });
});

test("Study workspace reports an actionable local service outage", async ({ page }) => {
  await page.route("**/api/studies", (route) => route.abort("failed"));
  await page.goto("/");
  await nav(page, "Studies");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Start local Docker Tempo and retry");
});

test("prepared explanation is self assessed offline and replayed through server validation", async ({ page }) => {
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const card = { id: "explanation-card", queue_entry_id: 971, cycle: 0, latest_review_id: 0, revision: 1,
    start_fen: fen, moves: [], content_type: "study_exercise", kind: "exercise",
    repertoire_id: null, repertoire_name: "Synthetic study", repertoire_source: "Study",
    study_id: "explanation-study", study_exercise_id: "explanation-exercise",
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "explanation-exercise", revision: 1,
      fen, specification: { type: "explanation", prompt: "Explain the knight placement", hint: "", explanation: "",
        rubric: "The knight starts on g1." } } };
  const queue = { local_date: localDate, count: 1, cards: [card] };
  await page.route("**/api/queue/window?**", (route) => route.fulfill({ json: queue }));
  await page.route("**/api/queue/prepared?**", (route) => route.fulfill({ json: { ...queue,
    prepared_at: new Date().toISOString(), projection: { state: "ready", generation: 1,
      updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } } }));
  await page.goto("/");
  await expect(page.getByText("Explain the knight placement")).toBeVisible();
  await waitForPreparedPhoneShell(page, localDate);
  await expect.poll(async () => page.evaluate(async () => {
    const request = indexedDB.open("tempo-offline-training", 2);
    const database = await new Promise<IDBDatabase>((resolve) => { request.onsuccess = () => resolve(request.result); });
    const read = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    return new Promise<number>((resolve) => { read.onsuccess = () => resolve(read.result?.cards?.length ?? 0); });
  })).toBe(1);
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await page.getByLabel("Optional written answer").fill("The knight develops from g1.");
  await page.getByRole("button", { name: "I have my answer" }).click();
  await expect(page.getByText("The knight starts on g1.")).toBeVisible();
  await page.getByRole("button", { name: "Correct (self-assessed)" }).click();
  await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
  const submissions: unknown[] = [];
  let selfAssessments = 0;
  await page.unroute("**/api/**");
  await page.route("**/api/studies/explanation-study/exercises/explanation-exercise/attempts", (route) => {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ json: { attempt_id: "pending", assessment: { outcome: "needs_self_assessment", feedback: "Compare" }, pending_self_assessment: true } });
  });
  await page.route("**/api/studies/explanation-study/exercises/explanation-exercise/attempts/*/self-assess", (route) => {
    selfAssessments += 1;
    return route.fulfill({ json: { attempt_id: "saved", rating: "correct", assessment: { outcome: "needs_self_assessment", feedback: "Saved" },
      review: { review_id: 973, requeue_entry_id: null } } });
  });
  await page.reload();
  await expect.poll(() => selfAssessments).toBe(1);
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ answer: { type: "explanation", text: "The knight develops from g1.", ready: true },
    revision: 1, queue_entry_id: 971 });
});
