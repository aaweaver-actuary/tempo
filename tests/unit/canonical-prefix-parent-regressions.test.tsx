import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RepertoireView from "../../app/views/repertoire_view";
import { publishNotification, notifications, clearNotificationHistory } from "../../app/lib/notifications";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: (input: string) => fetch(input),
}));
afterEach(() => { vi.unstubAllGlobals(); clearNotificationHistory(); });
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const emptyPrefix = { moves_uci: [], san: "", ending_fen: startingFen, revision: 0 };

async function openParent(onQueueChanged: () => Promise<void>, reloadFails = false) {
  let committed = false;
  const mutationIdentities: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options?: RequestInit) => {
    const url = String(input);
    if (options?.method === "PUT") {
      committed = true;
      mutationIdentities.push(new Headers(options.headers).get("Idempotency-Key")!);
      return Response.json({ ...emptyPrefix, revision: 1 });
    }
    if (url.endsWith("/repertoires")) {
      if (committed && reloadFails) return Response.json({ detail: "Repertoire read unavailable" }, { status: 503 });
      return Response.json({ repertoires: [{ id: "rep", name: "Italian", source_name: "italian.pgn", line_count: 1,
        card_count: 1, due_count: 0, canonical_prefix: { ...emptyPrefix, revision: committed ? 1 : 0 } }] });
    }
    if (options?.method === "POST") return Response.json({ ...emptyPrefix, preview_id: "preview", state: "checking" });
    if (url.includes("/preview/")) return Response.json({ ...emptyPrefix, preview_id: "preview", state: "ready", error: null,
      suggestion: emptyPrefix, conflicts: [], conflict_count: 0, next_cursor: null });
    return Response.json(emptyPrefix);
  }));
  render(<RepertoireView imported={[]} onImport={vi.fn()}
    onRepair={vi.fn()} onDeleteLocal={vi.fn()} onRenameLocal={vi.fn()}
    onQueueChanged={onQueueChanged} />);
  await screen.findByText("Italian");
  fireEvent.click(screen.getByText("•••"));
  fireEvent.click(screen.getByRole("menuitem", { name: "Canonical prefix…" }));
  await screen.findByText("Compatible. Saving will remove the opening restriction.");
  fireEvent.click(screen.getByRole("button", { name: "Save prefix" }));
  return mutationIdentities;
}

it("confirmed canonical prefix and repertoire reload dismiss while queue publication remains pending", async () => {
  let finishQueueRefresh!: () => void;
  const queueRefresh = vi.fn(() => new Promise<void>(resolve => { finishQueueRefresh = resolve; }));
  const mutationIdentities = await openParent(queueRefresh);
  await waitFor(() => expect(queueRefresh).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(mutationIdentities).toHaveLength(1);
  expect(mutationIdentities[0]).toBeTruthy();
  await act(async () => { finishQueueRefresh(); });
});

it("confirmed canonical prefix dismisses and retains separate training queue error reporting", async () => {
  const queueRefresh = vi.fn(async () => {
    publishNotification({ severity: "error", source: "training", message: "Today's queue is still preparing. Check Activity." });
    throw new Error("Queue publication pending");
  });
  const mutationIdentities = await openParent(queueRefresh);
  await waitFor(() => expect(queueRefresh).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(notifications()).toEqual(expect.arrayContaining([expect.objectContaining({ source: "training", severity: "error" })]));
  expect(mutationIdentities).toHaveLength(1);
});

it("confirmed canonical prefix with failed repertoire reload keeps committed recovery without resubmitting", async () => {
  const queueRefresh = vi.fn(async () => {});
  const mutationIdentities = await openParent(queueRefresh, true);
  await screen.findByText("Prefix saved. Reload the repertoire to see the updated analysis.");
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Save prefix" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save prefix" }));
  expect(mutationIdentities).toHaveLength(1);
  expect(queueRefresh).not.toHaveBeenCalled();
});

it("confirmed canonical prefix reload remains successful with an unreadable deletion recovery journal", async () => {
  localStorage.setItem("tempo-pending-repertoire-delete-v1", "unreadable");
  const queueRefresh = vi.fn(async () => {});
  const mutationIdentities = await openParent(queueRefresh);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(queueRefresh).toHaveBeenCalledOnce();
  expect(mutationIdentities).toHaveLength(1);
  expect(screen.getByRole("alert").textContent).toContain("Pending repertoire deletion unavailable");
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBe("unreadable");
});
