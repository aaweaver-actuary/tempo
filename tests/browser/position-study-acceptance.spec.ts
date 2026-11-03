import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { Chess } from "chess.js";
import { expect, test } from "./observability";
import { move } from "./native-board";
import { assertDisposableTarget } from "./disposable-target";
import { resetPostgresDeletedCards, verifyPostgresFixtureTarget } from "./product-fixture-reset";
import { prepareUI } from "./ui-fixtures";
import type { Page, Response } from "@playwright/test";
// The existing runner owns this JavaScript resource-admission helper.
import { validateDevelopmentBudgetOwner, waitForStudyQuiet } from "../../scripts/development-budget.mjs";

/*
 * This fixture is permitted only in the PostgreSQL browser runner's disposable
 * Compose project. It creates authored source and synthetic *initial* states;
 * it never inserts a review, first response, issued plan, or completion.
 * The running API/foreground worker owns issuance and the browser supplies the
 * actual moves. The runner owns project-scoped cleanup, including its volume.
 */
const fixtureProgram = String.raw`
import json, os, sys
from datetime import date, datetime, timedelta, timezone
import psycopg
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory

command, rep = sys.argv[1:3]
assert rep.startswith('position-browser-') and len(rep[17:]) == 32 and rep[17:].isalnum()
dsn = os.environ['TEMPO_DATABASE_READ_URL'] if command == 'read' else os.environ['TEMPO_DATABASE_WRITE_URL']
assert dsn.startswith('postgresql://tempo_reader@postgres:5432/tempo') if command == 'read' else dsn.startswith('postgresql://tempo_writer@postgres:5432/tempo')
with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
    assert raw.execute("SELECT current_database()").fetchone()[0] == 'tempo'
    assert raw.execute("SELECT current_user").fetchone()[0] == ('tempo_reader' if command == 'read' else 'tempo_writer')
    port = PostgresConnection(raw)
    now = datetime.now(timezone.utc)
    if command in ('seed', 'legacy'):
        import chess
        from fsrs import Card
        from app.queue_commands import current_study_day
        from tempo_legacy_database.adapters.queue_refresh.application.publication import begin_projection, publish_projection
        day = current_study_day(port)
    if command == 'seed':
        from tempo_evidence_math.position_learning import Hold
        from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
        from app.services.position_queue_admission import prepare_due, admit_due
        assert not raw.execute('SELECT 1 FROM repertoires WHERE id=%s', (rep,)).fetchone()
        raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Position browser','fixture',%s)", (rep,now.isoformat()))
        raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,3,'ready',%s)", (rep,now.isoformat()))
        # N1's complete legal Italian Knight Attack and Giuoco Piano.
        lines = [('a',['e2e4','e7e5','g1f3','b8c6','f1c4','g8f6','f3g5','d7d5','e4d5']),
                 ('b',['e2e4','e7e5','g1f3','b8c6','f1c4','f8c5','c2c3'])]
        for suffix, uci_moves in lines:
            route, card = rep+'-'+suffix, rep+'-card-'+suffix
            moves = json.dumps(uci_moves)
            raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,'white',%s,%s,%s)", (route,rep,route,chess.STARTING_FEN,moves,now.isoformat()))
            fsrs = Card(card_id=10001+(suffix=='b'),due=now-timedelta(days=2)).to_json()
            raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
                VALUES(%s,%s,'prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (card,rep,chess.STARTING_FEN,moves,day,fsrs,(date.fromisoformat(day)-timedelta(days=2)).isoformat()))
            raw.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (rep,card))
            raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
                VALUES(%s,3,%s,0,%s,%s,%s,%s,'white')""", (rep,route,card,chess.STARTING_FEN,chess.STARTING_FEN,moves))
        raw.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
            VALUES(%s,'clean','idle',%s,%s,2,2)""", (rep,rep+'-scan:2',now.isoformat()))
        for suffix, kind, generation, payload in [('graph','opening_graph_rebuild',3,{}),('scan','integrity_scan',2,{'repertoire_id':rep,'graph_generation':3})]:
            raw.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
                VALUES(%s,%s,%s,%s,'complete',%s,%s,%s,%s)""", (rep+'-'+suffix,kind,rep,generation,json.dumps(payload),now.isoformat(),now.isoformat(),now.isoformat()))
        raw.commit()
        source = prepare_position_source(port, rep)
        assert not isinstance(source, Hold), source
        digest = source.authored_token.authored_digest
        raw.execute('INSERT INTO study_position_epochs VALUES(%s,%s,%s,%s)', (rep,'browser-epoch',digest,now))
        raw.execute("INSERT INTO study_position_owners VALUES(%s,'browser-epoch','active',%s)", (rep,now))
        definitions = {}
        for route in source.routes:
            for occurrence in route.decisions:
                definitions[occurrence.definition.key.position] = occurrence.definition
        assert len(definitions) == 6
        root = ' '.join(chess.STARTING_FEN.split()[:4])
        giuoco = chess.Board()
        for move in lines[1][1][:6]: giuoco.push_uci(move)
        giuoco_target = ' '.join(giuoco.fen().split()[:4])
        for position, definition in definitions.items():
            ident = raw.execute("SELECT nextval('study_position_fsrs_id_seq')").fetchone()[0]
            due = now-timedelta(days=3 if position == root else 2 if position == giuoco_target else 1)
            card = Card(card_id=ident,due=due)
            raw.execute("""INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                VALUES(%s,%s,%s,%s,%s,'browser-epoch',%s,%s,%s,%s,%s)""", (rep,definition.key.variant,position,definition.expected_uci,definition.policy_version,digest,ident,card.to_json(),due,now-timedelta(days=2)))
        raw.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(%s,1,'position-browser',%s) ON CONFLICT(queue_date) DO NOTHING", (day,now.isoformat()))
        raw.commit()
        payload = {}
        for _ in range(8):
            item = prepare_due(port, day, payload)
            assert not isinstance(item, Hold), item
            if item is None or 'repertoire_done' in item: break
            assert admit_due(port, day, item) is None
            raw.commit()
            payload = item['cursor']
        rows = raw.execute('SELECT card_id FROM daily_queue WHERE queue_date=%s AND card_id IN (%s,%s) AND status=\'queued\' ORDER BY position,id', (day,rep+'-card-a',rep+'-card-b')).fetchall()
        assert [row[0] for row in rows] == [rep+'-card-a',rep+'-card-b'], rows
        begin_projection(port,day); publish_projection(port,day,now.isoformat(),0)
        raw.commit()
        print(json.dumps({'day':day, 'cards':[row[0] for row in rows]}))
    elif command == 'legacy':
        # The preceding position boards were genuinely completed in the UI.
        assert raw.execute("SELECT count(*) FROM daily_queue WHERE queue_date=%s AND card_id IN (%s,%s) AND status='queued'", (day,rep+'-card-a',rep+'-card-b')).fetchone()[0] == 0
        legacy, card = rep+'-legacy', rep+'-legacy-card'
        moves = json.dumps(['e2e4','e7e5','g1f3'])
        raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Legacy browser','fixture',%s)", (legacy,now.isoformat()))
        raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Legacy','white',%s,%s,%s)", (legacy+'-route',legacy,chess.STARTING_FEN,moves,now.isoformat()))
        raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
            VALUES(%s,%s,'prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (card,legacy,chess.STARTING_FEN,moves,day,Card(card_id=10003,due=now).to_json(),(date.fromisoformat(day)-timedelta(days=2)).isoformat()))
        raw.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (legacy,card))
        raw.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,admission_repertoire_id)
            VALUES(%s,%s,0,(SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s),'explicit',%s)""", (day,card,day,legacy))
        begin_projection(port,day); publish_projection(port,day,now.isoformat(),0)
        raw.commit()
        print(json.dumps({'card':card}))
    elif command == 'read':
        plans = [dict(row) for row in raw.execute('SELECT attempt_id,queue_entry_id,card_id,plan_json FROM study_position_issued_plans WHERE repertoire_id=%s ORDER BY issued_at,attempt_id', (rep,)).fetchall()]
        reviews = [dict(row) for row in raw.execute("""SELECT receipt.attempt_id,receipt.card_id,receipt.queue_entry_id,receipt.outcome,receipt.guided,review.source_kind
            FROM review_attempt_receipts receipt JOIN reviews review ON review.id=receipt.review_id
            WHERE receipt.card_id IN (%s,%s) ORDER BY receipt.review_id""", (rep+'-card-a',rep+'-card-b')).fetchall()]
        grades = [dict(row) for row in raw.execute('SELECT attempt_id,position,grade FROM study_position_grades WHERE repertoire_id=%s ORDER BY accepted_at,effect_id', (rep,)).fetchall()]
        physical = {row['id']:row['fsrs_card_json'] for row in raw.execute('SELECT id,fsrs_card_json FROM cards WHERE id IN (%s,%s)', (rep+'-card-a',rep+'-card-b')).fetchall()}
        print(json.dumps({'plans':plans,'reviews':reviews,'grades':grades,'physical':physical},default=str))
    else:
        raise ValueError('unknown fixture operation')
`;

type WirePlan = {
  plan_id: string; attempt_id: string; original_card_id: string; original_card_revision: number;
  queue_entry_id: number; starting_fen: string; moves_uci: string[];
  decision_slots: Array<{ move_offset: number; expected_uci: string }>;
};
type Queue = { cards: Array<{ id: string; queue_entry_id: number; position_study_mode?: string;
  position_presentation?: WirePlan | null; position_study_hold?: string | null }> };
type Snapshot = { reviews: Array<{ attempt_id: string; outcome: string; guided: boolean; source_kind: string }>;
  grades: Array<{ attempt_id: string; grade: string }>; physical: Record<string, string> };

function fixture<T>(project: string, action: "seed" | "read" | "legacy", identity: string): T {
  // Execute inside already-budgeted runner services: no extra Compose container.
  const service = ["exec", "-T", action === "read" ? "api" : "foreground-worker"];
  return JSON.parse(execFileSync("docker", ["compose", "-p", project,
    "-f", "docker-compose.postgres.test.yml", ...service,
    "python", "-c", fixtureProgram, action, identity], { encoding: "utf8", timeout: 30_000 }));
}

async function openingEvents(page: Page, attemptId: string) {
  return page.evaluate((id) => new Promise<Array<{ attempt_id: string; kind: string; response_uci: string | null; disposition: string | null }>>((resolve, reject) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const database = open.result;
      const request = database.transaction("opening_events").objectStore("opening_events").index("attempt_id").getAll(id);
      request.onsuccess = () => { database.close(); resolve(request.result); };
      request.onerror = () => { database.close(); reject(request.error); };
    };
  }), attemptId);
}

async function preparedEntryIds(page: Page): Promise<number[]> {
  return page.evaluate(() => new Promise<number[]>((resolve) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onerror = () => resolve([]);
    open.onsuccess = () => {
      const database = open.result;
      const request = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      request.onsuccess = () => { database.close(); resolve((request.result?.cards ?? []).map((card: { queue_entry_id: number }) => card.queue_entry_id)); };
      request.onerror = () => { database.close(); resolve([]); };
    };
  }));
}

async function preparedPositionPlan(page: Page, entryId: number): Promise<string | null> {
  return page.evaluate((id) => new Promise<string | null>((resolve) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onerror = () => resolve(null);
    open.onsuccess = () => {
      const database = open.result;
      const request = database.transaction("training").objectStore("training").get("prepared-daily-queue");
      request.onsuccess = () => { database.close();
        const card = (request.result?.cards ?? []).find((item: { queue_entry_id: number }) => item.queue_entry_id === id);
        resolve(card?.position_presentation?.plan_id ?? null); };
      request.onerror = () => { database.close(); resolve(null); };
    };
  }), entryId);
}

async function attemptForCard(page: Page, cardId: string): Promise<string | null> {
  return page.evaluate((id) => new Promise<string | null>((resolve) => {
    const open = indexedDB.open("tempo-offline-training", 2);
    open.onerror = () => resolve(null);
    open.onsuccess = () => {
      const database = open.result;
      const request = database.transaction("opening_attempts").objectStore("opening_attempts").getAll();
      request.onsuccess = () => { database.close();
        const found = request.result.find((row: { manifest?: { card_id?: string } }) => row.manifest?.card_id === id);
        resolve(found?.attempt_id ?? null); };
      request.onerror = () => { database.close(); resolve(null); };
    };
  }), cardId);
}

function position(fen: string): string { return fen.split(" ").slice(0, 4).join(" "); }

async function waitForProtectedStudyQuiet() {
  // JS inference omits the helper's optional signal without a default value.
  const options: Parameters<typeof waitForStudyQuiet>[0] & { signal: AbortSignal } = {
    signal: AbortSignal.timeout(30_000),
  };
  await waitForStudyQuiet(options);
}

/* iPhone standalone enables the real offline preparation path and service worker. */
test.use({ viewport: { width: 390, height: 844 }, userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1" });

test("position Study keeps its server-issued suffix and first-wrong journal across reload and offline", async ({ page, request, context, baseURL }, testInfo) => {
  test.setTimeout(180_000);
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("Position acceptance requires the owning disposable PostgreSQL browser runner");
  const budgetToken = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
  if (!budgetToken) throw new Error("The disposable browser runner must own the development budget");
  await validateDevelopmentBudgetOwner();
  const origin = process.env.TEMPO_DOCKER_URL;
  if (!origin || baseURL !== origin || (process.env.TEMPO_BROWSER_API_URL && process.env.TEMPO_BROWSER_API_URL !== `${origin}/api`))
    throw new Error("Browser and API must use the owning disposable endpoint");
  const address = new URL(origin);
  if (address.protocol !== "http:" || address.hostname !== "127.0.0.1" || !address.port ||
      address.pathname !== "/" || address.username || address.password || address.search || address.hash)
    throw new Error("Disposable endpoint must be a loopback origin with an explicit port");
  const api = `${origin}/api`;
  for (const service of ["web", "api", "foreground-worker"]) {
    const container = execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml",
      "ps", "-q", service], { encoding: "utf8", timeout: 5_000 }).trim();
    if (!container) throw new Error(`Missing disposable ${service} service`);
    const labels = JSON.parse(execFileSync("docker", ["inspect", "--format", "{{json .Config.Labels}}", container],
      { encoding: "utf8", timeout: 5_000 })) as Record<string, string>;
    if (labels["com.docker.compose.project"] !== project || labels["com.docker.compose.service"] !== service || labels["tempo.resource-owner"] !== budgetToken)
      throw new Error(`Unowned ${service} service`);
    if (service === "web") {
      const ports = JSON.parse(execFileSync("docker", ["inspect", "--format", "{{json .NetworkSettings.Ports}}", container],
        { encoding: "utf8", timeout: 5_000 }));
      const bindings = ports?.["80/tcp"];
      if (!Array.isArray(bindings) || bindings.length !== 1 || bindings[0].HostIp !== "127.0.0.1" || bindings[0].HostPort !== address.port)
        throw new Error("Browser endpoint does not belong to the owning disposable project");
    }
  }
  const healthResponse = await request.get(`${api}/health`);
  const health: unknown = healthResponse.ok() ? await healthResponse.json() : null;
  assertDisposableTarget(health);
  await verifyPostgresFixtureTarget({ health, apiUrl: api, project });
  await waitForProtectedStudyQuiet();
  // Existing product initialization, executed only after endpoint and ownership verification.
  const catalog = await (await request.get(`${api}/tactics/catalog`)).json();
  const activePackIds = catalog.packs.filter((pack: { active: boolean }) => pack.active).map((pack: { id: string }) => pack.id);
  if (activePackIds.length) {
    const response = await request.put(`${api}/tactics/activation`, { data: { pack_ids: activePackIds, active: false } });
    expect(response.ok(), await response.text()).toBe(true);
    await expect.poll(async () => (await (await request.get(`${api}/tactics/catalog`)).json()).packs.filter((pack: { active: boolean }) => pack.active)).toEqual([]);
  }
  const repertoires = (await (await request.get(`${api}/repertoires`)).json()).repertoires;
  for (const item of repertoires) {
    if (item.id.startsWith("__")) continue;
    const removal = await request.delete(`${api}/repertoires/${item.id}`);
    expect(removal.ok(), await removal.text()).toBe(true);
    if (removal.status() === 202) {
      const operationId = (await removal.json()).operation_id;
      await expect.poll(async () => (await (await request.get(`${api}/operations/${operationId}`)).json()).state, { timeout: 20_000 }).toBe("complete");
    }
  }
  await resetPostgresDeletedCards({ health, apiUrl: api, project });
  for (const card of (await (await request.get(`${api}/queue/today`)).json()).cards)
    if (card.content_type !== "opening") await request.delete(`${api}/cards/${card.id}`);
  const settings = await (await request.get(`${api}/settings`)).json();
  const updatedSettings = await request.put(`${api}/settings`, { data: { ...settings, new_cards_per_day: 2, initial_depth: 2, lichess_username: "", chesscom_username: "" } });
  expect(updatedSettings.ok(), await updatedSettings.text()).toBe(true);
  const identity = `position-browser-${randomUUID().replaceAll("-", "")}`;
  const requestTimingSamples: Array<{
    operation: "queue_window" | "accepted_review_http";
    elapsed_ms: number;
    http_status: number;
  }> = [];
  let unavailableQueueTimingCount = 0;
  let unavailableAcceptedReviewTimingCount = 0;
  let timingCaptureErrorCount = 0;
  const acceptedReviewTimingTasks: Promise<void>[] = [];
  context.on("response", response => {
    const request = response.request();
    const responseUrl = new URL(response.url());
    const cardAReview = `${api}/cards/${identity}-card-a/review`;
    const cardBReview = `${api}/cards/${identity}-card-b/review`;
    if (request.method() !== "POST" ||
        (responseUrl.origin + responseUrl.pathname !== cardAReview && responseUrl.origin + responseUrl.pathname !== cardBReview))
      return;
    const timingTask = (async () => {
      await response.finished();
      if (!response.ok()) return;
      const result: unknown = await response.json();
      if (!result || typeof result !== "object" || !("persisted" in result) || result.persisted !== true) return;
      const elapsedMs = request.timing().responseEnd;
      if (Number.isFinite(elapsedMs) && elapsedMs >= 0) {
        requestTimingSamples.push({ operation: "accepted_review_http", elapsed_ms: elapsedMs, http_status: response.status() });
      } else {
        unavailableAcceptedReviewTimingCount += 1;
      }
    })();
    acceptedReviewTimingTasks.push(timingTask.catch(() => {
      timingCaptureErrorCount += 1;
    }));
  });
  await context.addInitScript(() => Object.defineProperty(navigator, "standalone", { value: true, configurable: true }));
  await waitForProtectedStudyQuiet();
  fixture(project, "seed", identity);
  const before = fixture<Snapshot>(project, "read", identity);
  // This synthetic fixture observes the app's issuing requests. A separate
  // queue GET can itself issue the head and race the app's card-edit lock.
  const isAppQueue = (url: string, method: string) => {
    const parsed = new URL(url);
    return method === "GET" && parsed.origin + parsed.pathname === `${api}/queue/window`;
  };
  // Match the request start, not an old response that finishes after navigation.
  const nextAppQueue = () => page.waitForRequest(request => isAppQueue(request.url(), request.method()));
  const nextQueueAfterReview = () => {
    let accepted: Response | undefined;
    const onResponse = (response: Response) => {
      if (response.request().method() === "POST" && response.url() === `${api}/cards/${identity}-card-a/review` && response.ok())
        accepted = response;
    };
    page.on("response", onResponse);
    const pending = page.waitForRequest(request => accepted !== undefined && isAppQueue(request.url(), request.method()));
    return (async () => {
      try {
        const next = await pending;
        await accepted!.finished();
        expect((await accepted!.json()).persisted).toBe(true);
        return next;
      } finally {
        page.off("response", onResponse);
      }
    })();
  };
  const readAppQueue = async (pending: ReturnType<typeof nextAppQueue>): Promise<Queue> => {
    const request = await pending;
    const response = await request.response();
    if (!response) throw new Error("App queue request did not receive a response");
    await response.finished();
    const body = await response.body();
    const elapsedMs = response.request().timing().responseEnd;
    if (Number.isFinite(elapsedMs) && elapsedMs >= 0) {
      requestTimingSamples.push({ operation: "queue_window", elapsed_ms: elapsedMs, http_status: response.status() });
    } else {
      unavailableQueueTimingCount += 1;
    }
    expect(response.ok(), body.toString()).toBe(true);
    return JSON.parse(body.toString()) as Queue;
  };
  const positionHead = (queue: Queue, cardId: string): WirePlan => {
    const head = queue.cards[0];
    expect(head?.id).toBe(cardId);
    expect(head?.position_study_mode).toBe("position");
    expect(head?.position_study_hold).toBeFalsy();
    expect(head?.position_presentation).toBeTruthy();
    expect(head?.position_presentation?.original_card_id).toBe(cardId);
    expect(head?.position_presentation?.queue_entry_id).toBe(head?.queue_entry_id);
    return head.position_presentation!;
  };
  const initialQueue = nextAppQueue();
  await prepareUI(page);
  const first = positionHead(await readAppQueue(initialQueue), `${identity}-card-a`);
  expect(first.moves_uci).toEqual(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5", "d7d5", "e4d5"]);
  await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(first.queue_entry_id));
  await expect.poll(async () => position((await page.locator(".board-frame").first().getAttribute("data-fen")) ?? ""))
    .toBe(position(new Chess(first.starting_fen).fen()));
  const firstBoard = new Chess(first.starting_fen);
  let successorQueue: ReturnType<typeof nextAppQueue> | undefined;
  for (const [index, uci] of first.moves_uci.entries()) {
    if (firstBoard.turn() === "w") {
      await expect.poll(async () => position((await page.locator(".board-frame").first().getAttribute("data-fen")) ?? ""))
        .toBe(position(firstBoard.fen()));
      if (index === first.moves_uci.length - 1) successorQueue = nextQueueAfterReview();
      await move(page, uci.slice(0, 2), uci.slice(2, 4));
    }
    firstBoard.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || undefined });
  }
  await expect.poll(() => fixture<Snapshot>(project, "read", identity).reviews.some(r =>
    r.attempt_id === first.attempt_id && r.source_kind === "position_study" && r.outcome === "correct" && !r.guided), { timeout: 30_000, intervals: [1000, 2000] }).toBe(true);
  expect(successorQueue).toBeDefined();
  const suffix = positionHead(await readAppQueue(successorQueue!), `${identity}-card-b`);
  expect(suffix.attempt_id).not.toBe(first.attempt_id);
  expect(suffix.original_card_revision).toBe(1);
  expect(suffix.moves_uci).toEqual(["f8c5", "c2c3"]);
  expect(new Chess(suffix.starting_fen).turn()).toBe("b");
  const suffixReloadQueue = nextAppQueue();
  await page.reload();
  expect(positionHead(await readAppQueue(suffixReloadQueue), `${identity}-card-b`)).toEqual(suffix);
  await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(suffix.queue_entry_id));
  const afterCue = new Chess(suffix.starting_fen);
  afterCue.move({ from: "f8", to: "c5" });
  await expect.poll(async () => position((await page.locator(".board-frame").first().getAttribute("data-fen")) ?? ""))
    .toBe(position(afterCue.fen()));
  await move(page, "d2", "d4");
  await expect.poll(async () => (await openingEvents(page, suffix.attempt_id)).some(event =>
    event.kind === "first_response" && event.response_uci === "d2d4" && event.disposition === "wrong")).toBe(true);
  await expect.poll(() => fixture<Snapshot>(project, "read", identity).grades.some(g =>
    g.attempt_id === suffix.attempt_id && g.grade === "again"), { timeout: 30_000, intervals: [1000, 2000] }).toBe(true);
  const partial = fixture<Snapshot>(project, "read", identity);
  expect(partial.reviews.some(r => r.attempt_id === suffix.attempt_id)).toBe(false);
  expect(partial.grades.filter(g => g.attempt_id === suffix.attempt_id && g.grade === "again")).toHaveLength(1);
  const journal = await openingEvents(page, suffix.attempt_id);
  const wrong = journal.find(event => event.kind === "first_response" && event.disposition === "wrong")!;
  const replayQueue = nextAppQueue();
  await page.reload();
  const replay = positionHead(await readAppQueue(replayQueue), `${identity}-card-b`);
  expect(replay).toEqual(suffix);
  await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(suffix.queue_entry_id));
  const recovered = await openingEvents(page, suffix.attempt_id);
  expect(recovered.find(event => event.kind === "first_response" && event.disposition === "wrong")).toEqual(wrong);
  expect(recovered.filter(event => event.kind === "first_response" && event.disposition === "wrong")).toHaveLength(1);
  expect(fixture<Snapshot>(project, "read", identity).physical).toEqual(before.physical);
  // A controlled-browser run blocks service workers and cannot establish this.
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller));
  await expect.poll(() => preparedPositionPlan(page, suffix.queue_entry_id), { timeout: 30_000 }).toBe(suffix.plan_id);
  await context.setOffline(true);
  try {
    await page.reload();
    await expect(page.getByText(/Position study requires a connection/i).first()).toBeVisible();
    expect((await openingEvents(page, suffix.attempt_id)).find(event =>
      event.kind === "first_response" && event.disposition === "wrong")).toEqual(wrong);
  } finally {
    await context.setOffline(false);
  }

  // Complete the real guided position attempt before introducing a separate
  // legacy card; no retained issued work is removed or manually completed.
  const reconnectedQueue = nextAppQueue();
  await page.reload();
  expect(positionHead(await readAppQueue(reconnectedQueue), `${identity}-card-b`)).toEqual(suffix);
  await move(page, "c2", "c3");
  await expect.poll(() => fixture<Snapshot>(project, "read", identity).reviews.some(r =>
    r.attempt_id === suffix.attempt_id && r.source_kind === "position_study" && r.outcome === "again"),
    { timeout: 30_000, intervals: [1000, 2000] }).toBe(true);
  await waitForProtectedStudyQuiet();
  const legacy = fixture<{ card: string }>(project, "legacy", identity);
  const legacyQueue = nextAppQueue();
  await page.reload();
  const legacyHead = (await readAppQueue(legacyQueue)).cards[0];
  expect(legacyHead?.id).toBe(legacy.card);
  expect(legacyHead.position_study_mode).toBeUndefined();
  await expect.poll(() => preparedEntryIds(page), { timeout: 30_000 }).toContain(legacyHead.queue_entry_id);
  await context.setOffline(true);
  try {
    await page.reload();
    await move(page, "d2", "d4");
    await expect.poll(() => attemptForCard(page, legacy.card)).not.toBeNull();
    const legacyAttempt = (await attemptForCard(page, legacy.card))!;
    await expect.poll(async () => (await openingEvents(page, legacyAttempt)).some(event =>
      event.kind === "first_response" && event.response_uci === "d2d4" && event.disposition === "wrong")).toBe(true);
    const legacyWrong = (await openingEvents(page, legacyAttempt)).find(event =>
      event.kind === "first_response" && event.disposition === "wrong");
    await page.reload();
    expect(await attemptForCard(page, legacy.card)).toBe(legacyAttempt);
    expect((await openingEvents(page, legacyAttempt)).find(event =>
      event.kind === "first_response" && event.disposition === "wrong")).toEqual(legacyWrong);
    const durable = fixture<Snapshot>(project, "read", identity);
    expect(durable.grades.filter(g => g.attempt_id === suffix.attempt_id && g.grade === "again")).toHaveLength(1);
    expect(durable.reviews.some(r => r.attempt_id === suffix.attempt_id && r.source_kind === "position_study" && r.outcome === "again")).toBe(true);
  } finally {
    await context.setOffline(false);
  }
  await Promise.all(acceptedReviewTimingTasks);
  await testInfo.attach("position-study-request-timings", {
    body: Buffer.from(JSON.stringify({
      source: "synthetic_disposable_browser_journey",
      queue_elapsed_boundary: "Playwright app request.timing().responseEnd after response.finished(), relative to request start; not server transaction duration",
      accepted_review_elapsed_boundary: "Playwright request.timing().responseEnd after response.finished(), relative to request start",
      samples: requestTimingSamples,
      unavailable_queue_timing_count: unavailableQueueTimingCount,
      unavailable_accepted_review_timing_count: unavailableAcceptedReviewTimingCount,
      timing_capture_error_count: timingCaptureErrorCount,
    }, null, 2)),
    contentType: "application/json",
  });
});
