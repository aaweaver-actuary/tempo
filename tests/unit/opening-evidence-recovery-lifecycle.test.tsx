import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

const browserStorage = localStorage;
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });

it("AS-15 orphan completion verification timeout yields and retries safely", async () => {
  const state = await openingEvidenceStorage(); const completion = evidenceCompletion("orphan"); state.seed(completion);
  const original = structuredClone(state.stores.opening_attempts.get("orphan"));
  const journal = await import("../../app/lib/opening-evidence-journal");
  vi.useFakeTimers(); let release!: (response: Response) => void; let verificationSignal: AbortSignal | null | undefined;
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (init?.method === "POST") { posts.push(JSON.parse(init.body as string).attempt_id);
      return Promise.resolve(Response.json({ persisted: true, attempt_id: "orphan", received_sequences: [1, 2, 3], contiguous_sequence: 3 })); }
    expect(url).toContain("/attempts/orphan"); verificationSignal = init?.signal;
    return new Promise<Response>((resolve, reject) => { release = resolve;
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Verification aborted", "AbortError")), { once: true }); });
  }));
  let failed = false; let settled = false;
  const active = journal.recoverOpeningEvidence(); expect(journal.recoverOpeningEvidence()).toBe(active);
  const first = active.catch(() => { failed = true; }).finally(() => { settled = true; });
  try {
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(15_000);
    expect(settled).toBe(true); expect(failed).toBe(true); expect(verificationSignal?.aborted).toBe(true);
    expect(state.stores.opening_attempts.get("orphan")).toEqual(original); expect(posts).toEqual([]); expect(state.commits).toEqual([]);
    vi.mocked(fetch).mockImplementation(async (_url, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
      if (init?.method !== "POST") return Response.json({}, { status: 404 });
      const checkpoint = JSON.parse(init.body as string); expect(checkpoint.terminal).toEqual({ ...completion.terminal, state: "partial" }); posts.push(checkpoint.attempt_id);
      return Response.json({ persisted: true, attempt_id: "orphan", received_sequences: [1, 2, 3], contiguous_sequence: 3 });
    });
    expect(await journal.recoverOpeningEvidence()).toEqual({ moreWork: false }); expect(posts).toEqual(["orphan"]);
    expect(state.stores.opening_attempts.has("orphan")).toBe(false);
  } finally { release?.(Response.json({}, { status: 404 })); await first; }
});

it("AS-15 orphan verification response body shares its bounded deadline", async () => {
  const state = await openingEvidenceStorage(); state.seed(evidenceCompletion("orphan-body"));
  const original = structuredClone(state.stores.opening_attempts.get("orphan-body"));
  const journal = await import("../../app/lib/opening-evidence-journal"); vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => new Response(new ReadableStream({
    start(controller) { init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Body aborted", "AbortError")), { once: true }); },
  }))));
  const result = journal.recoverOpeningEvidence(); const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await vi.advanceTimersByTimeAsync(15_000); await rejected;
  expect(state.stores.opening_attempts.get("orphan-body")).toEqual(original); expect(state.commits).toEqual([]);
  expect(vi.getTimerCount()).toBe(0);
});
