import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import RepertoireView from "../../app/views/repertoire_view";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", () => ({
  readWorkspaceResponse: async () => Response.json({ repertoires: [{
    id: "rep", name: "Main", source_name: "main.pgn", line_count: 1,
    card_count: 2, due_count: 0, trained_color: "white",
  }] }),
  invalidateWorkspaceData: vi.fn(),
}));

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const startKey = startFen.split(" ").slice(0, 4).join(" ");

it("issue 4 opportunities explain promotion, degraded sources, and explicit actions", async () => {
  const onResolveGap = vi.fn();
  const onTrain = vi.fn();
  const onShowGamesAtPosition = vi.fn();
  const onBrowse = vi.fn();
  const opportunities = [
    {
      id: "weak", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
      fen_key: startKey, fen: startFen, card_id: "target", opponent_move_uci: null,
      trained_color: "white", score: 0.8, created_at: "2026-09-23T00:00:00Z",
      updated_at: "2026-09-23T00:00:00Z",
      evidence: { encounter_count: 11, success_count: 4, miss_count: 7, route_success_count: 11 },
    },
    {
      id: "gap", repertoire_id: "rep", kind: "missing_response", status: "active",
      fen_key: startKey, fen: startFen, card_id: null, opponent_move_uci: "e2e4",
      trained_color: "black", score: 0.2, created_at: "2026-09-23T00:00:00Z",
      updated_at: "2026-09-23T00:00:00Z",
      evidence: { maia_probability: 0.08, maia_status: "complete", explorer_probability: null,
        explorer_status: "failed", explorer_games: 0, personal_count: 5,
        coverage_node_id: "node" },
    },
    {
      id: "post-gap", repertoire_id: "rep", kind: "post_gap_weakness", status: "active",
      fen_key: startKey, fen: startFen, card_id: "existing", opponent_move_uci: "d2d4",
      trained_color: "black", score: 0.1, created_at: "2026-09-23T00:00:00Z",
      updated_at: "2026-09-23T00:00:00Z",
      evidence: { supporting_games: 2, max_loss_cp: 180, findings: [{ finding_id: "finding-1", game_id: "game-1", mistake_ply: 12, mistake_loss_cp: 180, analysis_version: 3 }] },
    },
  ];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/opportunities")) return Response.json({ opportunities });
    return Response.json({ dismissed: true });
  });
  vi.stubGlobal("fetch", fetcher);

  render(<RepertoireView imported={[]} onImport={vi.fn()} onBrowse={onBrowse}
    onResolveGap={onResolveGap} onShowGamesAtPosition={onShowGamesAtPosition} onRepair={vi.fn()} onDeleteLocal={vi.fn()}
    onRenameLocal={vi.fn()} onQueueChanged={async () => {}} onTrain={onTrain} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Opportunities" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Opportunities" }));
  await waitFor(() => expect(screen.getByText("Weak known decision")).toBeTruthy());
  expect(screen.getByText(/Reached 11 times · correct 4 · missed 7/)).toBeTruthy();
  expect(screen.getByText(/Lichess cohort unavailable \(failed/)).toBeTruthy();
  expect(screen.getByText(/2 supporting games · largest following mistake 180 cp/)).toBeTruthy();
  fireEvent.click(screen.getByText("Engine and game evidence"));
  expect(screen.getByText(/Game game-1 · ply 12 · loss 180 cp · analysis 3/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "View supporting games" }));
  expect(onShowGamesAtPosition).toHaveBeenCalledWith(startFen);
  fireEvent.click(screen.getByRole("button", { name: "Go to Train" }));
  expect(onTrain).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Browse existing repertoire" }));
  expect(onBrowse).toHaveBeenCalledWith("rep");
  fireEvent.click(screen.getByRole("button", { name: "Investigate branch" }));
  expect(onResolveGap).toHaveBeenCalledWith("rep", expect.objectContaining({ move_uci: "e2e4", gap_id: "node:e2e4" }));
});
