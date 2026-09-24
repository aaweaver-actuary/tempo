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
  Chessboard: ({ onSquareSelect }: { onSquareSelect?: (square: string) => void }) =>
    <button onClick={() => onSquareSelect?.("b4")}>Board square b4</button>,
}));

const card = {
  id: "defense-card", backendId: "defense-card", defenseCandidateId: "candidate",
  queueEntryId: 7, kind: "defense", startingFen: "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1",
  orientation: "white", encounterBadges: ["Seen recently"], encounterCount30d: 4,
  lastEncounteredAt: "2026-09-24T00:00:00Z",
} as unknown as PracticeCard;

it("defense recognition keeps findings hidden until the staged answer is submitted", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/recognition")) return Response.json({ status: "ready_for_move" });
    return Response.json({ candidate_id: "candidate", card_id: "defense-card",
      exercise_revision: 2, prompt: "What danger should your next move account for?",
      rubric_version: 2, recognition_required: true });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DefenseTrainingView card={card} boardTheme={{} as never} pieceSet={{} as never}
    useSharedBoard={false} onAdvance={async () => {}} />);
  await waitFor(() => expect(screen.getByText("Assess the position.", { exact: false })).toBeTruthy());
  expect(screen.queryByText("Seen recently")).toBeNull();
  expect(screen.queryByText("Knight route:", { exact: false })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Board square b4" }));
  fireEvent.click(screen.getByLabelText("No concrete threat"));
  fireEvent.click(screen.getByRole("button", { name: "Explain danger" }));
  expect(screen.getByText("What would happen if you ignored the danger?")).toBeTruthy();
  expect(screen.queryByText("Seen recently")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Continue to move" }));
  await waitFor(() => expect(screen.getByText("Now choose a move that addresses the position.")).toBeTruthy());
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
  await waitFor(() => expect(screen.getByRole("button", { name: "Explain danger" })).toBeTruthy());
  fireEvent.click(screen.getByLabelText("No concrete threat"));
  fireEvent.click(screen.getByRole("button", { name: "Explain danger" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue to move" }));
  await waitFor(() => expect(screen.getByText("The forking knight is capturable before it wins material.")).toBeTruthy());
  expect(screen.queryByText("Now choose a move that addresses the position.")).toBeNull();
  expect(screen.getByText("Seen recently")).toBeTruthy();
});
