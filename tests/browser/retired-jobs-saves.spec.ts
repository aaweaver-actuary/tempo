import { test, expect } from "./observability";
import { assertDisposableTarget } from "./disposable-target";
import { navigate, prepareUI } from "./ui-fixtures";

const api = process.env.TEMPO_BROWSER_API_URL ?? (process.env.TEMPO_DOCKER_URL
  ? `${process.env.TEMPO_DOCKER_URL}/api` : "http://127.0.0.1:8001/api");

test("retired Jobs and Saves stay absent during navigation and debug export", async ({ page, request }) => {
  const health = await request.get(`${api}/health`);
  assertDisposableTarget(health.ok() ? await health.json() : null);
  const retiredRequests: string[] = [];
  page.on("request", outgoing => {
    const path = new URL(outgoing.url()).pathname;
    if (/^\/api\/system\/activity(?:\/|$)/.test(path) ||
        (outgoing.method() === "GET" && (path === "/api/discoveries" || path.startsWith("/api/discovery-admissions/"))) ||
        (outgoing.method() === "POST" && /^\/api\/discoveries\/[^/]+\/accept$/.test(path)))
      retiredRequests.push(`${outgoing.method()} ${path}`);
  });
  await page.addInitScript(() => {
    localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{ opportunityId: "synthetic" }]));
    localStorage.setItem("tempo-retirement-test-sentinel", "preserved");
  });
  await prepareUI(page);
  await expect(page.getByRole("button", { name: "Open local data menu" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("tempo-pending-discovery-admissions-v1"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("tempo-retirement-test-sentinel"))).toBe("preserved");
  await expect(page.getByRole("button", { name: "Analysis activity" })).toHaveCount(0);
  await expect(page.getByText("Jobs", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Saved discovery requests", { exact: true })).toHaveCount(0);

  for (const [destination, heading] of [["Settings", "Settings"], ["Repertoire", "Repertoire"], ["Games", "Game review"]]) {
    await navigate(page, destination);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
  await page.evaluate(() => window.dispatchEvent(new ErrorEvent("error", {
    message: "Retirement diagnostic fixture", error: new Error("Retirement diagnostic fixture"),
  })));
  const panel = page.getByRole("region", { name: "Tempo error details" });
  await expect(panel).toBeVisible();
  const bundle = JSON.parse(await panel.getByRole("textbox", { name: "Debug information" }).inputValue());
  expect(bundle.schemaVersion).toBe(1);
  expect(bundle.error.message).toContain("Retirement diagnostic fixture");
  expect(bundle).not.toHaveProperty("serviceStatus");
  expect(bundle).not.toHaveProperty("backgroundDiagnostics");
  await panel.getByRole("button", { name: "Dismiss" }).click();
  await expect(panel).toHaveCount(0);
  expect(retiredRequests).toEqual([]);
});
