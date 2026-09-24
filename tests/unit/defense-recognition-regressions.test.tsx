import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it, vi } from "vitest";
import DefenseTrainingView from "../../app/views/defense_training_view";
import type { PracticeCard } from "../../app/types";

it("Docker owns defensive engine claims while the browser remains passive", () => {
  const homeSource = readFileSync(resolve(process.cwd(), "app/views/home_view.tsx"), "utf8");
  expect(homeSource).not.toContain("useDefensiveThreatAnalysis");
  expect(homeSource).not.toContain("/api/defensive-threats/analysis/claim");
});

vi.mock("../../app/hooks/use-board-publisher", () => ({
  useBoardPublisher: () => ({ setShellBoardForOwner: vi.fn(), releaseShellBoardForOwner: vi.fn() }),
}));
vi.mock("../../app/components/board-workspace", () => ({
  BoardTools: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("../../app/components/chessboard", () => ({
  Chessboard: ({ fen, onSquareSelect, shapes }: { fen: string; onSquareSelect?: (square: string) => void; shapes?: unknown[] }) =>
    <div data-testid="recognition-board" data-fen={fen} data-shapes={JSON.stringify(shapes)}>
      {["b4", "c2", "e1", "a1", "f5", "e3", "g2", "c4"].map((square) =>
        <button key={square} onClick={() => onSquareSelect?.(square)}>Board square {square}</button>)}
    </div>,
}));

const card = {
  id: "defense-card", backendId: "defense-card", defenseCandidateId: "candidate",
  queueEntryId: 7, kind: "defense", startingFen: "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1",
  orientation: "white", encounterBadges: ["Seen recently"], encounterCount30d: 4,
  lastEncounteredAt: "2026-09-24T00:00:00Z",
} as unknown as PracticeCard;
const previewFen = "4k3/8/8/8/1n6/P7/8/R3K3 b Q - 0 1";

it("guided defensive recognition reveals board arrows only after the assessment", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/recognition")) return Response.json({ status: "ready_for_move",
      recognition_correct: true, feedback: { knight_route: [{ from_square: "b4", to_square: "c2" }],
        fork_geometry: { knight_to: "c2", king: { square: "e1" },
          major: { square: "a1", piece: "rook" } }, sound_moves: [], refutation_uci: [] } });
    return Response.json({ candidate_id: "candidate", card_id: "defense-card",
      exercise_revision: 2, prompt: "What danger should your next move account for?",
      rubric_version: 3, recognition_required: true, proposed_move_uci: "a2a3",
      proposed_move_san: "a3", preview_fen: previewFen, fork_move_san: "Nc2+" });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DefenseTrainingView card={card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByText("Select the piece that could create the danger.")).toBeTruthy());
  expect(screen.getByTestId("recognition-board").getAttribute("data-fen")).toBe(previewFen);
  expect(screen.getByText("Consider", { exact: false })).toBeTruthy();
  expect(screen.queryByText("Seen recently")).toBeNull();
  expect(screen.getByTestId("recognition-board").getAttribute("data-shapes")).not.toContain('"brush":"red"');
  fireEvent.click(screen.getByRole("button", { name: "Board square b4" }));
  fireEvent.click(screen.getByRole("button", { name: "Board square c2" }));
  fireEvent.click(screen.getByRole("button", { name: "Board square e1" }));
  fireEvent.click(screen.getByRole("button", { name: "Board square a1" }));
  expect(screen.getByText("What would happen if you ignored the danger?")).toBeTruthy();
  expect(screen.queryByText("Seen recently")).toBeNull();
  fireEvent.change(screen.getByLabelText("Consequence"), { target: { value: "checking_fork" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit assessment" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Continue to defense" })).toBeTruthy());
  expect(screen.getByTestId("recognition-board").getAttribute("data-shapes")).toContain('"brush":"red"');
  fireEvent.click(screen.getByRole("button", { name: "Continue to defense" }));
  expect(screen.getByTestId("recognition-board").getAttribute("data-fen")).toBe(card.startingFen);
  expect(screen.getByTestId("recognition-board").getAttribute("data-shapes")).not.toContain('"brush":"red"');
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("validated false alarm ends after explanation without requesting a move", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
    Response.json(String(input).endsWith("/recognition")
      ? { status: "correct", diagnostic: "Refuted fork", recognition_correct: true,
          defense_status: "not_applicable", feedback: {
            knight_route: [], fork_geometry: null, sound_moves: [],
            refutation_uci: ["a2a3", "b4c2", "e4c2"],
            control_explanation: "The forking knight is capturable before it wins material.",
          } }
      : { candidate_id: "candidate", card_id: "defense-card", exercise_revision: 2,
          prompt: "What danger should your next move account for?", recognition_required: true,
          rubric_version: 3, proposed_move_uci: "a2a3", proposed_move_san: "a3", preview_fen: previewFen })));
  render(<DefenseTrainingView card={card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "No concrete threat" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "No concrete threat" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit assessment" }));
  await waitFor(() => expect(screen.getByText("The forking knight is capturable before it wins material.")).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Continue to defense" })).toBeNull();
  expect(screen.getByText("Seen recently")).toBeTruthy();
});

it("reported Rc4 recognition uses the after-move rook square with keyboard and touch", async () => {
  const originalFen = "8/k1p2p2/1p2p3/1P2Pn2/PR6/8/3r1PKP/8 w - - 3 36";
  const afterRc4Fen = "8/k1p2p2/1p2p3/1P2Pn2/P1R5/8/3r1PKP/8 b - - 4 36";
  const rc4Card = { ...card, startingFen: originalFen } as unknown as PracticeCard;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => Response.json(
    String(input).endsWith("/recognition")
      ? { status: "ready_for_move", recognition_correct: true,
          feedback: { knight_route: [{ from_square: "f5", to_square: "e3" }],
            fork_geometry: { knight_to: "e3", king: { square: "g2" },
              major: { square: "c4", piece: "rook" } }, sound_moves: [],
            refutation_uci: ["b4c4", "f5e3", "g2g3", "e3c4"] } }
      : { candidate_id: "candidate", card_id: "defense-card", exercise_revision: 3,
          rubric_version: 3, recognition_required: true, proposed_move_uci: "b4c4",
          proposed_move_san: "Rc4", preview_fen: afterRc4Fen, fork_move_san: "Ne3+" }));
  vi.stubGlobal("fetch", fetcher);
  render(<DefenseTrainingView card={rc4Card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByText("Preview after 36.Rc4 · Black to play")).toBeTruthy());
  expect(screen.getByTestId("recognition-board").getAttribute("data-fen")).toBe(afterRc4Fen);
  expect(screen.getByTestId("recognition-board").getAttribute("data-shapes")).not.toContain('"brush":"red"');
  fireEvent.change(screen.getByLabelText("Dangerous piece square"), { target: { value: "f5" } });
  fireEvent.keyDown(screen.getByLabelText("Dangerous piece square"), { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Board square e3" }));
  fireEvent.click(screen.getByRole("button", { name: "Board square g2" }));
  fireEvent.click(screen.getByRole("button", { name: "Board square c4" }));
  fireEvent.change(screen.getByLabelText("Consequence"), { target: { value: "checking_fork" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit assessment" }));
  await waitFor(() => expect(screen.getByText(/After 36.Rc4, Ne3\+ checks the king/)).toBeTruthy());
  const shapes = screen.getByTestId("recognition-board").getAttribute("data-shapes") ?? "";
  expect(shapes).toContain('"orig":"f5","dest":"e3"');
  expect(shapes).not.toContain('"orig":"g8"');
  fireEvent.click(screen.getByRole("button", { name: "Continue to defense" }));
  expect(screen.getByTestId("recognition-board").getAttribute("data-fen")).toBe(originalFen);
});
