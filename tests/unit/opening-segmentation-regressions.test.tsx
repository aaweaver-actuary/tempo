import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OpeningSegmentation } from "../../app/components/opening-segmentation";
import { segmentationListSchema } from "../../app/domain/opening-segmentation";
vi.mock("../../app/components/chessboard", () => ({ Chessboard: ({ fen }: { fen: string }) => <div data-testid="advisory-board">{fen}</div> }));
vi.mock("../../app/lib/operation-status", () => ({ confirmOperationResponse: async (response: Response) => response }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const recommendation = { id: "rec", repertoire_id: "rep", kind: "shared_trunk", decisions_before: 48,
  decisions_after: 20, decisions_avoided: 28, additional_starts: 1, segment_count: 9 };
const list = { version: 1, preview_only: true, state: "ready", content_version: 3, graph_generation: 2,
  error: null, invalidated_pins: 0, recommendations: [recommendation] };
const detail = { version: 1, preview_only: true, content_version: 3, graph_generation: 2, source_fingerprint: "sources",
  recommendation, rationale: "Practice the shared opening once.", estimate_basis: "structural count",
  routes: [{ id: "route", name: "Legal route" }], next_route: null, next_segment: null,
  segments: [{ id: "trunk", role: "shared_trunk", starting_fen: "start", ending_fen: "end", moves: ["e2e4"], tested_decisions: 4, trained_color: "white" }] };

it("AS-01 preview explains 48-to-20 savings and has no Apply action", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => { expect(options?.method).toBeUndefined(); return Response.json(url.endsWith("/rec") ? detail : list); });
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
    return Response.json(url.endsWith("/rec") ? detail : list);
  }));
  render(<OpeningSegmentation repertoireId="rep" theme="brown" pieceSet="cburnett" initiallyOpened />);
  fireEvent.click(await screen.findByRole("button", { name: "Preview shared opening" }));
  fireEvent.click(await screen.findByRole("button", { name: "Keep current presentation" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(JSON.parse(String(calls[0].body))).toEqual({ choice: "keep_current", content_version: 3, graph_generation: 2, source_fingerprint: "sources" });
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
