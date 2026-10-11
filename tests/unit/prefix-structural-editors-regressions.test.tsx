import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalysisPasteDialog } from "../../app/AnalysisPasteDialog";
import RepertoireView from "../../app/views/repertoire_view";
import { prefixTransitionPlanSchema, transitionSummary } from "../../app/domain/prefix-transition";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import transition from "../fixtures/prefix-comparison/transition.json";
import structural from "../fixtures/prefix-comparison/structural.json";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: (input: string) => fetch(input),
}));
beforeEach(() => { installPrefixTransitionTestLocks(); });
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });
const prefixKey = "tempo-pending-prefix-transition-v1:rep";
const initialFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const prefix = { moves_uci: [], san: "", ending_fen: initialFen, revision: 0 };
const summary = transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black");
function savedReservation(affectedRepertoireIds = summary.affectedRepertoireIds) {
  localStorage.setItem(prefixKey, JSON.stringify({ version: 1, operationId: "prefix-operation", repertoireId: "rep", request: transition.request,
    summary: { ...summary, affectedRepertoireIds }, state: "pending", approval: { plan: transition.ready, source: { repertoire_id: structural.source.repertoire_id, snapshot_id: structural.source.snapshot_id, graph_generation: structural.source.graph_generation, lines: structural.source.lines } } }));
}
async function openLibrary() {
  const mutations: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: unknown, options?: RequestInit) => {
    const url = String(input);
    if (options?.method === "DELETE" || options?.method === "PUT") {
      mutations.push(options);
      return options.method === "DELETE" ? Response.json({ deleted: true, id: "rep" }) : Response.json({ ...prefix, revision: 1 });
    }
    if (url.endsWith("/repertoires")) return Response.json({ repertoires: [
      { id: "rep", name: "Combined Black", source_name: "opening.pgn", line_count: 2, card_count: 5, due_count: 0, trained_color: "black" },
      { id: "independent", name: "Independent", source_name: "other.pgn", line_count: 1, card_count: 1, due_count: 0 },
    ] });
    if (options?.method === "POST") return Response.json({ ...prefix, preview_id: "preview", state: "checking" });
    if (url.includes("/preview/")) return Response.json({ ...prefix, preview_id: "preview", state: "ready", error: null,
      suggestion: prefix, conflicts: [], conflict_count: 0, next_cursor: null });
    return Response.json(prefix);
  }));
  render(<RepertoireView imported={[]} onImport={vi.fn()} onBrowse={vi.fn()} onResolveGap={vi.fn()}
    onShowGamesAtPosition={vi.fn()} onRepair={vi.fn()} onDeleteLocal={vi.fn()} onRenameLocal={vi.fn()}
    onQueueChanged={vi.fn()} onTrain={vi.fn()} />);
  await screen.findByRole("heading", { name: "Combined Black" });
  return mutations;
}
function openMenu(name = "Combined Black") {
  const card = screen.getByRole("heading", { name }).closest("article")!;
  fireEvent.click(card.querySelector("details.card-menu summary")!);
  return within(card);
}
it("issue81_already_open_deletion_dialog_pauses_after_storage_notification", async () => {
  const mutations = await openLibrary();
  fireEvent.click(openMenu().getByRole("menuitem", { name: "Delete" }));
  await screen.findByRole("button", { name: "Delete repertoire" });
  await act(async () => { savedReservation(); window.dispatchEvent(new StorageEvent("storage", { key: prefixKey })); });
  await waitFor(() => expect((screen.getByRole("button", { name: "Delete repertoire" }) as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Delete repertoire" }));
  expect(mutations).toHaveLength(0);
  expect(screen.getByRole("dialog").textContent).toContain("Resolve the prefix transition");
});
it("issue81_delayed_storage_notification_cannot_authorize_open_deletion_dialog", async () => {
  const mutations = await openLibrary();
  fireEvent.click(openMenu().getByRole("menuitem", { name: "Delete" }));
  savedReservation(); // Deliberately omit notification so the rendered confirmation remains stale.
  const confirm = screen.getByRole("button", { name: "Delete repertoire" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(false);
  fireEvent.click(confirm);
  await screen.findAllByText("Resolve the prefix transition affecting this repertoire before changing its structure.");
  expect(mutations).toHaveLength(0);
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});
it("issue81_already_open_canonical_confirmation_cannot_save_after_reservation", async () => {
  const mutations = await openLibrary();
  fireEvent.click(openMenu().getByRole("menuitem", { name: "Canonical prefix…" }));
  await screen.findByText("Compatible. Saving will remove the opening restriction.");
  savedReservation();
  fireEvent.click(screen.getByRole("button", { name: "Save prefix" }));
  await screen.findAllByText("Resolve the prefix transition affecting this repertoire before changing its structure.");
  expect(mutations).toHaveLength(0);
});
it("issue81_corrupt_summary_pauses_target_and_reports_recovery_error", async () => {
  const mutations = await openLibrary();
  await act(async () => { savedReservation([]); window.dispatchEvent(new StorageEvent("storage", { key: prefixKey })); });
  const menu = openMenu();
  expect((menu.getByRole("menuitem", { name: "Delete" }) as HTMLButtonElement).disabled).toBe(true);
  expect(mutations).toHaveLength(0);
});
it("issue81_independent_repertoire_structural_controls_remain_editable", async () => {
  await openLibrary();
  await act(async () => { savedReservation(); window.dispatchEvent(new StorageEvent("storage", { key: prefixKey })); });
  const menu = openMenu("Independent");
  expect((menu.getByRole("menuitem", { name: "Delete" }) as HTMLButtonElement).disabled).toBe(false);
  expect((menu.getByRole("menuitem", { name: "Canonical prefix…" }) as HTMLButtonElement).disabled).toBe(false);
});

it("issue81_paused_paste_editor_can_check_completed_receipt_without_erasing_unsaved_input", async () => {
  savedReservation();
  localStorage.setItem("tempo-pending-analysis-paste-v1", JSON.stringify({ operationId: "earlier-save", body: "earlier approved body" }));
  const fetcher = vi.fn(async () => Response.json({ state: "complete", response: { saved: [], affected_repertoire_ids: ["rep"], gap_resolved: false } }));
  vi.stubGlobal("fetch", fetcher);
  const onSaved = vi.fn();
  render(<AnalysisPasteDialog context={{}} onClose={vi.fn()} onSaved={onSaved} />);
  fireEvent.change(screen.getByRole("textbox", { name: "SAN or PGN" }), { target: { value: "1. d4 d5" } });
  fireEvent.click(await screen.findByRole("button", { name: "Check earlier save" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(["rep"], []));
  expect((screen.getByRole("textbox", { name: "SAN or PGN" }) as HTMLTextAreaElement).value).toBe("1. d4 d5");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0]).toHaveLength(1);
  expect((screen.getByRole("button", { name: "Save selected lines" }) as HTMLButtonElement).disabled).toBe(true);
});

it("issue81_open_deletion_dialog_pauses_when_storage_publication_capability_disappears", async () => {
  const mutations = await openLibrary();
  fireEvent.click(openMenu().getByRole("menuitem", { name: "Delete" }));
  fireEvent.click(screen.getByLabelText("Delete learned cards"));
  await act(async () => {
    vi.stubGlobal("MessageChannel", undefined);
    window.dispatchEvent(new StorageEvent("storage", { key: prefixKey }));
  });
  expect((screen.getByRole("button", { name: "Delete repertoire" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByLabelText("Delete learned cards") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(false);
  expect(mutations).toHaveLength(0);
});
