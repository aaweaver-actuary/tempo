import { afterEach, expect, it, vi } from "vitest";
import { setPackActivation } from "../../app/lib/tactical-catalog";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("tactic activation pending command reuses its key across retry", async () => {
  const postKeys: string[] = [];
  let activationCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/"))
      return Response.json({ state: "pending" });
    expect(url).toContain("/api/tactics/activation");
    postKeys.push(new Headers(options?.headers).get("Idempotency-Key") ?? "");
    activationCalls += 1;
    return activationCalls === 1
      ? Response.json({ operation_id: "activation-operation" }, { status: 202 })
      : Response.json({ version: 1, groups: [], themes: [], packs: [] });
  }));

  await expect(setPackActivation(["pack-1"], true)).rejects.toThrow("still pending");
  await expect(setPackActivation(["pack-1"], true)).resolves.toEqual({
    version: 1, groups: [], themes: [], packs: [],
  });
  expect(postKeys).toHaveLength(2);
  expect(postKeys[0]).toBeTruthy();
  expect(postKeys[1]).toBe(postKeys[0]);
});

it("recovers the legacy activation key with reversed pack order and removes it after completion", async () => {
  const key = "tempo-tactics-activation-pending-v1:pack-a,pack-b:true";
  localStorage.setItem(key, "legacy-activation");
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => {
    expect(new Headers(options.headers).get("Idempotency-Key")).toBe("legacy-activation");
    expect(JSON.parse(String(options.body))).toEqual({ pack_ids: ["pack-b", "pack-a"], active: true });
    expect(localStorage.getItem(key)).toBe("legacy-activation");
    return Response.json({ version: 1, groups: [], themes: [], packs: [] });
  }));
  await setPackActivation(["pack-b", "pack-a"], true);
  expect(localStorage.getItem(key)).toBeNull();
});

it.each(["missing", "foreign"])("%s activation acknowledgment retains its key and does not poll a foreign receipt", async kind => {
  const fetcher = vi.fn(async () => Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(setPackActivation(["pack-1"], true)).rejects.toThrow("still pending");
  expect(localStorage.getItem("tempo-tactics-activation-pending-v1:pack-1:true")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledOnce();
});
