import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OpeningSegmentation } from "../../app/components/opening-segmentation";
import { segmentationListSchema } from "../../app/domain/opening-segmentation";
vi.mock("../../app/components/chessboard", () => ({ Chessboard: ({ fen }: { fen: string }) => <div data-testid="advisory-board">{fen}</div> }));
vi.mock("../../app/lib/operation-status", () => ({ confirmOperationResponse: async (response: Response) => response }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const recommendation = { id: "rec", snapshot_id: "snapshot-one", repertoire_id: "rep", kind: "shared_trunk", decisions_before: 48,
  decisions_after: 20, decisions_avoided: 28, additional_starts: 1, segment_count: 9 };
const list = { version: 1, preview_only: true, state: "ready", content_version: 3, graph_generation: 2,
  error: null, invalidated_pins: 0, recommendations: [recommendation] };
const detail = { version: 1, preview_only: true, content_version: 3, graph_generation: 2, source_fingerprint: "sources", snapshot_id: "snapshot-one",
  recommendation, rationale: "Practice the shared opening once.", estimate_basis: "structural count",
  routes: [{ id: "route", name: "Legal route" }], next_route: null, next_segment: null,
  segments: [{ id: "trunk", role: "shared_trunk", starting_fen: "start", ending_fen: "end", moves: ["e2e4"], tested_decisions: 4, trained_color: "white" }] };

it("AS-01 preview explains 48-to-20 savings and has no Apply action", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => { expect(options?.method).toBeUndefined(); return Response.json((url.includes("/rec?") || url.endsWith("/rec")) ? detail : list); });
  vi.stubGlobal("fetch", fetcher);
  render(<OpeningSegmentation repertoireId="rep" theme="brown" pieceSet="cburnett" initiallyOpened />);
  await screen.findByText(/48 → 20/);
  fireEvent.click(screen.getByRole("button", { name: "Preview shared opening" }));
  await screen.findByText("Practice the shared opening once.");
  expect(screen.getAllByTestId("advisory-board")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
  expect(fetcher.mock.calls.every(call => call[1]?.method === undefined)).toBe(true);
});

it("AS-14 keep-current sends observed versions and only confirmed metadata removes advice", async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") { calls.push(options); return Response.json({ saved: true }); }
    return Response.json((url.includes("/rec?") || url.endsWith("/rec")) ? detail : list);
  }));
  render(<OpeningSegmentation repertoireId="rep" theme="brown" pieceSet="cburnett" initiallyOpened />);
  fireEvent.click(await screen.findByRole("button", { name: "Preview shared opening" }));
  fireEvent.click(await screen.findByRole("button", { name: "Keep current presentation" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(JSON.parse(String(calls[0].body))).toEqual({ choice: "keep_current", content_version: 3, graph_generation: 2, source_fingerprint: "sources", snapshot_id: "snapshot-one" });
});

it("AS-19 network failure stays actionable without demonstration recommendations", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Database unavailable. Reconnect and retry." }, { status: 503 })));
  render(<OpeningSegmentation repertoireId="rep" theme="brown" pieceSet="cburnett" initiallyOpened />);
  await screen.findByText("Database unavailable. Reconnect and retry.");
  expect(screen.queryByText(/48 → 20/)).toBeNull();
});

it("versioned recommendation contract rejects executable or unsupported payloads", () => {
  expect(segmentationListSchema.safeParse({ ...list, preview_only: false }).success).toBe(false);
  expect(segmentationListSchema.safeParse({ ...list, version: 2 }).success).toBe(false);
});

function mountPreview() {
  return render(<OpeningSegmentation repertoireId="rep" theme="brown" pieceSet="cburnett" initiallyOpened />);
}
async function openPreview() {
  fireEvent.click(await screen.findByRole("button", { name: "Preview shared opening" }));
  await screen.findByText(detail.rationale);
}
function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

it("normal segment and route pagination remains bound to one displayed snapshot", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    if (!(url.includes("/rec?") || url.endsWith("/rec"))) return Response.json(list);
    const query = new URL(url, "http://localhost").searchParams;
    expect(query.get("snapshot_id")).toBe("snapshot-one");
    if (query.has("after_segment")) return Response.json({ ...detail, segments: [{ ...detail.segments[0], id: "branch", moves: ["d2d4"] }] });
    if (query.has("after_route")) return Response.json({ ...detail, routes: [{ id: "route-two", name: "Second route" }] });
    return Response.json({ ...detail, next_segment: "trunk", next_route: "route" });
  }));
  mountPreview(); await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "More proposed segments" }));
  await screen.findByText("Moves: d2d4");
  fireEvent.click(screen.getByRole("button", { name: "More affected routes" }));
  await screen.findByText("Affected routes: Legal route; Second route");
  expect(screen.getByText("Moves: d2d4")).toBeTruthy();
  expect(urls.filter(url => (url.includes("/rec?") || url.endsWith("/rec")))).toHaveLength(3);
});

for (const page of ["segments", "routes"] as const) it(`rebuild between ${page} pages clears old content and suppresses preferences`, async () => {
  let reads = 0; let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") { posts++; return Response.json({ saved: true }); }
    if (!(url.includes("/rec?") || url.endsWith("/rec"))) return Response.json(list);
    if (++reads === 1) return Response.json({ ...detail, next_segment: "trunk", next_route: "route" });
    return Response.json({ ...detail, content_version: 4, snapshot_id: "snapshot-two",
      recommendation: { ...recommendation, snapshot_id: "snapshot-two" }, rationale: "New publication" });
  }));
  mountPreview(); await openPreview();
  fireEvent.click(screen.getByRole("button", { name: page === "segments" ? "More proposed segments" : "More affected routes" }));
  await screen.findByText(/preview snapshot changed/);
  expect(screen.queryByText(detail.rationale)).toBeNull();
  expect(screen.queryByText("New publication")).toBeNull();
  expect(screen.queryByRole("button", { name: "Keep current presentation" })).toBeNull();
  expect(posts).toBe(0);
});

for (const change of ["stale", "building", "failed", "removed", "snapshot"] as const) it(`list ${change} invalidates displayed preview and preference controls`, async () => {
  let listingReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") return Response.json({ queued: true });
    if ((url.includes("/rec?") || url.endsWith("/rec"))) return Response.json(detail);
    if (++listingReads === 1) return Response.json(list);
    return Response.json({ ...list, state: ["removed", "snapshot"].includes(change) ? "ready" : change,
      recommendations: change === "removed" ? [] : [{ ...recommendation, snapshot_id: "snapshot-two" }] });
  }));
  mountPreview(); await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Refresh recommendations" }));
  await waitFor(() => expect(listingReads).toBe(2));
  expect(screen.queryByText(detail.rationale)).toBeNull();
  expect(screen.queryByRole("button", { name: "Keep current presentation" })).toBeNull();
});

for (const outcome of ["success", "error"] as const) it(`late detail ${outcome} after closure cannot restore state or unlock a newer request`, async () => {
  const obsolete = deferredResponse(); const current = deferredResponse(); let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (url.includes("/rec?") || url.endsWith("/rec")) ? (++reads === 1 ? obsolete.promise : current.promise) : Response.json(list)));
  mountPreview();
  fireEvent.click(await screen.findByRole("button", { name: "Preview shared opening" }));
  fireEvent.click(screen.getByRole("button", { name: "Recommended segmentation" }));
  fireEvent.click(screen.getByRole("button", { name: "Recommended segmentation" }));
  fireEvent.click(await screen.findByRole("button", { name: "Preview shared opening" }));
  if (outcome === "success") obsolete.resolve(Response.json(detail)); else obsolete.reject(new Error("Obsolete failure"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Preview shared opening" }).hasAttribute("disabled")).toBe(true));
  expect(screen.queryByText(detail.rationale)).toBeNull();
  expect(screen.queryByText("Obsolete failure")).toBeNull();
  current.resolve(Response.json(detail)); await screen.findByText(detail.rationale);
  expect(screen.getByRole("button", { name: "Keep current presentation" }).hasAttribute("disabled")).toBe(false);
});

it("late list responses after repertoire change cannot overwrite current recommendations", async () => {
  const old = deferredResponse();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/rep/") ? old.promise : Response.json({ ...list, recommendations: [] })));
  const view = mountPreview();
  view.rerender(<OpeningSegmentation repertoireId="other" theme="brown" pieceSet="cburnett" initiallyOpened />);
  await screen.findByText(/No new recommendations/);
  old.resolve(Response.json(list));
  await waitFor(() => expect(screen.queryByText(/48 → 20/)).toBeNull());
});

it("late command success after invalidation cannot clear a newer preview", async () => {
  const old = deferredResponse();
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => options?.method === "POST" ? old.promise : Response.json((url.includes("/rec?") || url.endsWith("/rec")) ? detail : list)));
  mountPreview(); await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Keep current presentation" }));
  fireEvent.click(screen.getByRole("button", { name: "Recommended segmentation" }));
  fireEvent.click(screen.getByRole("button", { name: "Recommended segmentation" }));
  await openPreview(); old.resolve(Response.json({ saved: true }));
  await waitFor(() => expect(screen.getByText(detail.rationale)).toBeTruthy());
});

it("preference retries retain their idempotency key and displayed snapshot", async () => {
  const posts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") { posts.push(options); return Response.json({ detail: "Retry saving" }, { status: 503 }); }
    return Response.json((url.includes("/rec?") || url.endsWith("/rec")) ? detail : list);
  }));
  mountPreview(); await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Keep current presentation" }));
  await screen.findByText("Retry saving");
  fireEvent.click(screen.getByRole("button", { name: "Keep current presentation" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[0].headers).toEqual(posts[1].headers);
  expect(JSON.parse(String(posts[1].body)).snapshot_id).toBe("snapshot-one");
});
