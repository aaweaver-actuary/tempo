import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { test, expect, api, nav } from "./product-fixtures";
import { verifyPostgresFixtureTarget } from "./product-fixture-reset";
import { validateDevelopmentBudgetOwner, waitForStudyQuiet } from "../../scripts/development-budget.mjs";

// The only inserted row is a saved game, never a Study receipt or plan.
const program = String.raw`
import json, os, sys
import psycopg
from chess import STARTING_FEN
operation, ident = sys.argv[1:]
assert ident.startswith('browser-game-')
key = 'TEMPO_DATABASE_WRITE_URL' if operation == 'seed' else 'TEMPO_DATABASE_READ_URL'
with psycopg.connect(os.environ[key]) as db:
    assert db.execute('SELECT current_database()').fetchone()[0] == 'tempo'
    if operation == 'seed':
        assert db.execute('SELECT current_user').fetchone()[0] == 'tempo_writer'
        db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
            VALUES(%s,'fixture','fixture','2026-01-01','rapid',0,'white','1-0',%s,%s)""",
            (ident, STARTING_FEN, json.dumps(['e2e4','e7e5','f1c4'])))
    else:
        assert db.execute('SELECT current_user').fetchone()[0] == 'tempo_reader'
        tables = ('cards','daily_queue','queue_attempt_origins','reviews','review_attempt_receipts',
                  'study_position_issued_plans','study_position_grades','study_position_states','imported_games')
        print(json.dumps({table: db.execute('SELECT COALESCE(json_agg(t ORDER BY t::text), \'[]\'::json) FROM (SELECT * FROM ' + table + ') t').fetchone()[0] for table in tables}, default=str))
`;

test("minimal Game Review compares pasted and saved games against current repertoire without Study writes", async ({ page, request }) => {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("Disposable runner required");
  const health = await (await request.get(`${api}/health`)).json();
  await verifyPostgresFixtureTarget({ health, apiUrl: api, project });
  const token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
  if (!token) throw new Error("Development budget owner required");
  await validateDevelopmentBudgetOwner();
  const quiet = () => {
    const options: Parameters<typeof waitForStudyQuiet>[0] & { signal: AbortSignal } = { signal: AbortSignal.timeout(30_000) };
    return waitForStudyQuiet(options);
  };
  const fixture = (action: "seed" | "read", identity: string) => {
    const service = action === "seed" ? "foreground-worker" : "api";
    const id = execFileSync("docker", ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml", "ps", "-q", service], { encoding: "utf8" }).trim();
    const labels = JSON.parse(execFileSync("docker", ["inspect", "--format", "{{json .Config.Labels}}", id], { encoding: "utf8" }));
    expect(labels["com.docker.compose.project"]).toBe(project);
    expect(labels["com.docker.compose.service"]).toBe(service);
    expect(labels["tempo.resource-owner"]).toBe(token);
    return execFileSync("docker", ["exec", id, "python", "-c", program, action, identity], { encoding: "utf8", timeout: 15_000 });
  };
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: "game-review.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Current repertoire"]\n\n1. e4 e5 2. Nf3 Nc6 *') },
    trained_color: "white", initial_depth: "2",
  } });
  expect(imported.ok(), await imported.text()).toBeTruthy();
  let result = await imported.json();
  if (imported.status() === 202) {
    const operation = result.operation_id;
    await expect.poll(async () => { result = await (await request.get(`${api}/operations/${operation}`)).json(); return result.state; }, { timeout: 30_000 }).toBe("complete");
    result = result.response;
  }
  const repertoire = result.repertoire_id as string;
  expect(repertoire).toBeTruthy();
  await expect.poll(async () => (await (await request.get(`${api}/repertoires`)).json()).repertoires
    .find((item: { id: string }) => item.id === repertoire)?.graph_state, { timeout: 30_000 }).toBe("ready");
  const id = `browser-game-${randomUUID()}`;
  fixture("seed", id);
  await page.goto("/");
  await nav(page, "Game Review");
  await page.getByLabel("Repertoire").selectOption(repertoire);
  await page.waitForLoadState("networkidle");
  await quiet();
  // Establish a stable baseline after import and navigation have settled.
  let previous: string | undefined;
  await expect.poll(() => {
    const current = fixture("read", id);
    const stable = previous !== undefined && current === previous;
    previous = current;
    return stable;
  }, { timeout: 30_000, intervals: [1000] }).toBe(true);
  if (previous === undefined) throw new Error("Study snapshot unavailable");
  const before = JSON.parse(previous);
  await page.getByLabel("Paste PGN").fill('[Event "Played"]\n\n1. e4 e5 2. Bc4 *');
  const post = page.waitForResponse(response => response.url().endsWith("/api/product/game-review/compare") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Review PGN" }).click();
  const postResponse = await post;
  expect(postResponse.ok()).toBe(true);
  expect((await postResponse.json()).comparison).toMatchObject({ kind: "departure", ply: 3, played_uci: "f1c4", expected_uci: ["g1f3"] });
  await expect(page.getByText(/First departure at ply 3: f1c4; repertoire: g1f3/)).toBeVisible();
  const saved = page.waitForResponse(response => response.url().includes(`/api/product/game-review/games/${id}?`) && response.request().method() === "GET");
  await page.getByRole("button", { name: /fixture · 2026-01-01 · 1-0/ }).click();
  const savedResponse = await saved;
  expect(savedResponse.ok()).toBe(true);
  expect((await savedResponse.json()).comparison).toMatchObject({ kind: "departure", ply: 3, played_uci: "f1c4", expected_uci: ["g1f3"] });
  await expect(page.getByText(/First departure at ply 3: f1c4; repertoire: g1f3/)).toBeVisible();
  await page.waitForLoadState("networkidle");
  await quiet();
  expect(JSON.parse(fixture("read", id))).toEqual(before);
});
