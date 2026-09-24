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
  Chessboard: ({ onSquareSelect, shapes }: { onSquareSelect?: (square: string) => void; shapes?: unknown[] }) =>
    <div data-testid="recognition-board" data-shapes={JSON.stringify(shapes)}>
      {["b4", "c2", "e1", "a1"].map((square) =>
        <button key={square} onClick={() => onSquareSelect?.(square)}>Board square {square}</button>)}
    </div>,
}));

const card = {
  id: "defense-card", backendId: "defense-card", defenseCandidateId: "candidate",
  queueEntryId: 7, kind: "defense", startingFen: "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1",
  orientation: "white", encounterBadges: ["Seen recently"], encounterCount30d: 4,
  lastEncounteredAt: "2026-09-24T00:00:00Z",
} as unknown as PracticeCard;

it("guided defensive recognition reveals board arrows only after the assessment", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/recognition")) return Response.json({ status: "ready_for_move",
      recognition_correct: true, feedback: { knight_route: [{ from_square: "b4", to_square: "c2" }],
        fork_geometry: { knight_to: "c2", king: { square: "e1" },
          major: { square: "a1", piece: "rook" } }, sound_moves: [], refutation_uci: [] } });
    return Response.json({ candidate_id: "candidate", card_id: "defense-card",
      exercise_revision: 2, prompt: "What danger should your next move account for?",
      rubric_version: 2, recognition_required: true });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DefenseTrainingView card={card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByText("Select the piece that could create the danger.")).toBeTruthy());
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
  await waitFor(() => expect(screen.getByText("Now play a move that handles this danger.", { exact: false })).toBeTruthy());
  expect(screen.getByTestId("recognition-board").getAttribute("data-shapes")).toContain('"brush":"red"');
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
          prompt: "What danger should your next move account for?", recognition_required: true })));
  render(<DefenseTrainingView card={card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "No concrete threat" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "No concrete threat" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit assessment" }));
  await waitFor(() => expect(screen.getByText("The forking knight is capturable before it wins material.")).toBeTruthy());
  expect(screen.queryByText("Now play a move that handles this danger.", { exact: false })).toBeNull();
  expect(screen.getByText("Seen recently")).toBeTruthy();
});
