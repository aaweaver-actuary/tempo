import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CanonicalPrefixDialog } from "../../app/components/canonical-prefix-dialog";
import { requestCanonicalPrefixPreview, saveCanonicalPrefixCommand } from "../../app/lib/canonical-prefix-command";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: ({ fen }: { fen: string }) => <div data-testid="prefix-board">{fen}</div> }));
const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const italianFen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3";
const italian = { moves_uci: ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], san: "1. e4 e5 2. Nf3 Nc6 3. Bc4", ending_fen: italianFen, revision: 0 };
const empty = { moves_uci: [] as string[], san: "", ending_fen: startFen, revision: 0 };
const projection = (scope = empty) => ({ ...scope, preview_id: "preview", state: "ready", error: null,
  suggestion: italian, conflicts: [], conflict_count: 0, next_cursor: null });
const properties = () => ({ repertoireId: "rep", repertoireName: "Italian", side: "white" as const,
  theme: "brown" as const, pieceSet: "cburnett" as const, onClose: vi.fn(), onSaved: vi.fn(async () => {}) });

beforeEach(() => localStorage.clear());
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("canonical prefix suggests a shared opening without automatically applying it", async () => {
  let checked = empty;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      checked = JSON.parse(String(options.body)).movetext ? italian : empty;
      return Response.json({ ...checked, preview_id: "preview", state: "checking" });
    }
    if (options?.method === "PUT") return Response.json({ ...checked, revision: 1 });
    return Response.json(url.includes("/preview/") ? projection(checked) : empty);
  }));
  const props = properties(); render(<CanonicalPrefixDialog {...props} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Use shared opening" })).toBeTruthy());
  expect((screen.getByLabelText("Assumed SAN moves") as HTMLInputElement).value).toBe("");
  expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Use shared opening" }));
  await waitFor(() => expect(screen.getByText("Compatible. Discoveries start after this opening.")).toBeTruthy());
  expect(screen.getByTestId("prefix-board").textContent).toBe(italianFen);
  fireEvent.click(screen.getByRole("button", { name: "Save prefix" }));
  await waitFor(() => expect(props.onSaved).toHaveBeenCalledOnce());
  expect(props.onClose).toHaveBeenCalledOnce();
});

it("canonical prefix shows the first conflicting move and blocks save", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => Response.json(
    options?.method === "POST" ? { ...italian, preview_id: "preview", state: "checking" } :
      url.includes("/preview/") ? { ...projection(italian), state: "conflicts", conflict_count: 1,
        conflicts: [{ item_id: "line:philidor", name: "Philidor", reason: "Move 4: expected Nc6, found d6", disagreement_ply: 3 }] } : italian,
  )));
  render(<CanonicalPrefixDialog {...properties()} />);
  await waitFor(() => expect(screen.getByText(/expected Nc6, found d6/)).toBeTruthy());
  expect((screen.getByRole("button", { name: "Save prefix" }) as HTMLButtonElement).disabled).toBe(true);
  expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
});

it("canonical prefix clears the restriction only after a compatible preview is saved", async () => {
  let checked = italian;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      checked = JSON.parse(String(options.body)).movetext ? italian : empty;
      return Response.json({ ...checked, preview_id: "preview", state: "checking" });
    }
    return Response.json(url.includes("/preview/") ? projection(checked) : italian);
  }));
  render(<CanonicalPrefixDialog {...properties()} />);
  await waitFor(() => expect(screen.getByText("Compatible. Discoveries start after this opening.")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Clear prefix" }));
  await waitFor(() => expect(screen.getByText("Compatible. Saving will remove the opening restriction.")).toBeTruthy());
  expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
});

it("canonical prefix lost save acknowledgement recovers the original receipt without another write", async () => {
  const calls = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "PUT") throw new Error("Connection interrupted");
    return Response.json({ state: "complete", response: { ...italian, revision: 1 } });
  });
  vi.stubGlobal("fetch", calls);
  await expect(saveCanonicalPrefixCommand("rep", "preview", 0)).rejects.toThrow("interrupted");
  const recovered = await saveCanonicalPrefixCommand("rep", "preview", 0);
  expect(recovered.revision).toBe(1);
  expect(calls.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(1);
  expect(localStorage.getItem("tempo-canonical-prefix-save-v1:rep")).toBeNull();
});

it("canonical prefix pending preview retains operation identity and never accepts a different payload", async () => {
  const calls = vi.fn(async (_url: string, options?: RequestInit) => Response.json(
    options?.method === "POST" ? { operation_id: "operation", state: "pending" } : { state: "pending" },
    { status: options?.method === "POST" ? 202 : 200 },
  ));
  vi.stubGlobal("fetch", calls);
  await expect(requestCanonicalPrefixPreview("rep", "e4 e5")).rejects.toThrow("pending");
  const pending = localStorage.getItem("tempo-canonical-prefix-preview-v1:rep");
  await expect(requestCanonicalPrefixPreview("rep", "d4 d5")).rejects.toThrow("pending");
  expect(localStorage.getItem("tempo-canonical-prefix-preview-v1:rep")).toBe(pending);
  expect(calls.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});


it("canonical prefix interrupted before admission replays the same save identity and bytes", async () => {
  let delivered = false;
  const writes: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method !== "PUT") return Response.json({ detail: "Operation not found" }, { status: 404 });
    writes.push(options);
    if (!delivered) { delivered = true; throw new Error("Connection interrupted before send"); }
    return Response.json({ ...italian, revision: 1 });
  }));
  await expect(saveCanonicalPrefixCommand("rep", "preview", 0)).rejects.toThrow("interrupted");
  expect((await saveCanonicalPrefixCommand("rep", "preview", 0)).revision).toBe(1);
  expect(writes).toHaveLength(2);
  expect(writes[1].body).toBe(writes[0].body);
  expect(writes[1].headers).toEqual(writes[0].headers);
});

it("canonical prefix server failure preserves the pending save for receipt recovery", async () => {
  let saved = false;
  const writes: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method !== "PUT") return Response.json({ state: "complete", response: { ...italian, revision: 1 } });
    writes.push(options); saved = true;
    return Response.json({ detail: "Worker response lost" }, { status: 503 });
  }));
  await expect(saveCanonicalPrefixCommand("rep", "preview", 0)).rejects.toThrow("confirm");
  expect(saved).toBe(true);
  expect(localStorage.getItem("tempo-canonical-prefix-save-v1:rep")).not.toBeNull();
  expect((await saveCanonicalPrefixCommand("rep", "preview", 0)).revision).toBe(1);
  expect(writes).toHaveLength(1);
});

it("canonical prefix metadata survives the workspace repertoire and coverage contracts", async () => {
  const { validateWorkspacePayload } = await import("../../app/domain/adapters/workspace-adapters");
  const { repertoireCoverageSummarySchema } = await import("../../app/domain/schemas");
  const repertoire = { id: "rep", name: "Italian", source_name: "italian.pgn", line_count: 1, card_count: 1, due_count: 0, canonical_prefix: italian };
  expect(validateWorkspacePayload("/api/repertoires", { repertoires: [repertoire] })).toEqual({ repertoires: [repertoire] });
  expect(repertoireCoverageSummarySchema.parse({ run_id: null, status: "queued", required_branches: 0, covered_branches: 0,
    probability_coverage: null, is_complete: false, unknown_nodes: 0, settings: { canonical_prefix_revision: 1, automatic_priority: true,
    reply_denominator: 15, cumulative_target: 0.8, horizon_fullmoves: 15, path_floor: 0.01, maia_elo: 1500, explorer_rating: 1600,
    recent_median_rating: 1600, speed_weights: {}, cohort_games: 0 } }).settings?.canonical_prefix_revision).toBe(1);
});


it("canonical prefix reopening recovers an undelivered preview using its original text", async () => {
  localStorage.setItem("tempo-canonical-prefix-preview-v1:rep", JSON.stringify({ operationId: "lost-preview", body: { movetext: "e4 e5 Nf3 Nc6 Bc4" } }));
  const writes: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/operations/")) return Response.json({ detail: "Not found" }, { status: 404 });
    if (options?.method === "POST") {
      writes.push(options);
      return Response.json({ ...italian, preview_id: "preview", state: "checking" });
    }
    return Response.json(projection(italian));
  }));
  render(<CanonicalPrefixDialog {...properties()} />);
  await waitFor(() => expect(screen.getByText("Compatible. Discoveries start after this opening.")).toBeTruthy());
  expect((screen.getByLabelText("Assumed SAN moves") as HTMLInputElement).value).toBe(italian.san);
  expect(writes).toHaveLength(1);
  expect(writes[0].headers).toEqual({ "Content-Type": "application/json", "Idempotency-Key": "lost-preview" });
  expect(JSON.parse(String(writes[0].body)).movetext).toBe("e4 e5 Nf3 Nc6 Bc4");
});

it("canonical prefix confirmed save followed by refresh failure remains committed without a stale retry", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => Response.json(
    options?.method === "PUT" ? { ...italian, revision: 1 } :
      options?.method === "POST" ? { ...italian, preview_id: "preview", state: "checking" } :
        url.includes("/preview/") ? projection(italian) : italian,
  )));
  const props = { ...properties(), onSaved: vi.fn(async () => { throw new Error("Workspace refresh failed"); }) };
  render(<CanonicalPrefixDialog {...props} />);
  await waitFor(() => expect(screen.getByText("Compatible. Discoveries start after this opening.")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Save prefix" }));
  await waitFor(() => expect(screen.getByText(/Prefix saved.*Reload/)).toBeTruthy());
  expect(screen.queryByText(/Could not save/)).toBeNull();
  expect((screen.getByRole("button", { name: "Save prefix" }) as HTMLButtonElement).disabled).toBe(true);
  expect(localStorage.getItem("tempo-canonical-prefix-save-v1:rep")).toBeNull();
  expect(vi.mocked(fetch).mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(1);
});
