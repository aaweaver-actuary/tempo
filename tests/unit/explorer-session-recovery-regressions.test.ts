import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ensureExplorerSession } from "../../app/lib/explorer-session";
import { saveLichessSessionToken } from "../../app/lib/lichess-session";

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear(); });
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

describe("Explorer session recovery", () => {
  it("restores an unchanged browser credential after backend session loss without saving it durably", async () => {
    saveLichessSessionToken("synthetic-valid");
    const fetch = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ status: "registration_missing" }))
      .mockResolvedValueOnce(response({ registered: true }))
      .mockResolvedValueOnce(response({ status: "available" }));
    expect(await ensureExplorerSession()).toBe("available");
    expect(fetch.mock.calls[1][1]?.headers).toMatchObject({ Authorization: "Bearer synthetic-valid" });
    expect(await ensureExplorerSession()).toBe("available");
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.getItem("tempo-lichess-token")).toBe("synthetic-valid");
  });

  it("retains a rejected credential across service loss and reload until a different connection is provided", async () => {
    saveLichessSessionToken("synthetic-rejected");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ status: "credential_rejected" }));
    expect(await ensureExplorerSession()).toBe("credential_rejected");
    expect(await ensureExplorerSession()).toBe("credential_rejected");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("tempo-explorer-rejected-credential-v1")).not.toContain("synthetic");
    saveLichessSessionToken("synthetic-reconnected");
    fetch.mockResolvedValueOnce(response({ status: "registration_missing" })).mockResolvedValueOnce(response({ registered: true }));
    expect(await ensureExplorerSession()).toBe("available");
    expect(sessionStorage.getItem("tempo-explorer-rejected-credential-v1")).toBeNull();
  });

  it("coalesces concurrent registration requests for one connected credential", async () => {
    saveLichessSessionToken("synthetic-valid");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ status: "registration_missing" }))
      .mockResolvedValueOnce(response({ registered: true }));
    expect(await Promise.all([ensureExplorerSession(), ensureExplorerSession(), ensureExplorerSession()]))
      .toEqual(["available", "available", "available"]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps an unavailable store unknown and never displays false registration success", async () => {
    saveLichessSessionToken("synthetic-valid");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ detail: "unavailable" }, 503));
    await expect(ensureExplorerSession()).rejects.toThrow("unavailable");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("tempo-explorer-rejected-credential-v1")).toBeNull();
    fetch.mockResolvedValueOnce(response({ status: "registration_missing" }))
      .mockResolvedValueOnce(response({ registered: false }));
    await expect(ensureExplorerSession()).rejects.toThrow("could not be restored");
  });

  it("honors a raced server rejection and does not retry the same credential", async () => {
    saveLichessSessionToken("synthetic-rejected");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ status: "registration_missing" }))
      .mockResolvedValueOnce(response({ detail: { code: "explorer_credential_rejected" } }, 409));
    expect(await ensureExplorerSession()).toBe("credential_rejected");
    expect(await ensureExplorerSession()).toBe("credential_rejected");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

it("retains the Maia refresh and its durable receipt when Explorer registration is unavailable", async () => {
  const { requestCoverageRefresh } = await import("../../app/lib/coverage-refresh-command");
  saveLichessSessionToken("synthetic-valid");
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ detail: "unavailable" }, 503))
    .mockResolvedValueOnce(response({ run_id: "current-run", status: "queued" }));
  await requestCoverageRefresh("rep");
  expect(fetch.mock.calls[1][0]).toContain("/api/repertoires/rep/coverage/refresh");
  expect(fetch.mock.calls[1][1]?.headers).toHaveProperty("Idempotency-Key");
  expect(localStorage.length).toBe(0);
});
