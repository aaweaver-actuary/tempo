import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import Home from "../../app/views/legacy-home";
import { prepareLegacyWorkspaceNavigation } from "../../app/views/legacy-workspace";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { asCardId, asFenString, asRepertoireId, asSanMove } from "../../app/types";

vi.mock("../../app/views/fetchAndInitializeQueue", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/views/fetchAndInitializeQueue")>(), fetchAndInitializeQueue: vi.fn(),
}));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(), moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
const savedMoves = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
const positions = [new Chess().fen()];
const position = new Chess();
const route = savedMoves.map(san => { const move = position.move(san); positions.push(position.fen()); return { san: move.san, uci: `${move.from}${move.to}`, fen: position.fen() }; });
const card = { id: asCardId("training-card"), backendId: asCardId("saved-card"), repertoireId: asRepertoireId("najdorf"), kind: "opening" as const,
  title: "Najdorf", subtitle: "", startingFen: asFenString(positions[0]), moves: savedMoves.map(asSanMove), userMoveTarget: 5, orientation: "white" as const, revision: 1 };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async input => Response.json(String(input).includes("/repertoire/lines") ? { lines: [{ id: "authored-line", repertoire_id: "najdorf", repertoire_name: "Najdorf", name: "Saved line", trained_color: "white", start_fen: positions[0], moves: route.map(move => move.uci) }] } : { providers: [], states: [], repertoires: [], discoveries: [], annotations: [] })));
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    useTrainingStore.getState().hydrateLocalQueue([card], true);
    useTrainingStore.setState({ queueReadiness: "ready", step: 8, currentFenString: asFenString(positions[8]) });
    return "installed" as const;
  });
});

async function openTraining() {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("saved-card"));
  await waitFor(() => expect(useBoardShellStore.getState().board.fen).toBe(positions[8]));
}

it("retired view aliases fall back to Train and retired position actions are absent", async () => {
  for (const alias of ["analysis", "builder", "compare", "games", "insights", "progress", "statistics"] as const)
    expect(prepareLegacyWorkspaceNavigation(alias)).toBe("train");
  await openTraining();
  expect(screen.queryByLabelText("Open review position")).toBeNull();
  expect(screen.queryByRole("button", { name: "Analysis" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Builder" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Games here" })).toBeNull();
});
