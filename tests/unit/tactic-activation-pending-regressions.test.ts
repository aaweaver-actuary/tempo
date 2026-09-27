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
