import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";
import { useBoardShellStore } from "../../app/state/board-shell-store";

vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));
afterEach(() => vi.unstubAllGlobals());

const promotionFen = "7k/P7/8/8/8/8/8/7K w - - 0 1";

it.each(["q", "r", "b", "n"])("Study board promotion preserves the selected %s piece in FEN and submission", async (promotion) => {
  const submissions: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/present")) return Response.json({ id: "promotion-exercise", revision: 1, type: "move_line", mode: "single",
      prompt: "Promote on the board", hint: "", fen: promotionFen });
    if (String(input).endsWith("/attempts")) {
      submissions.push(JSON.parse(String(init?.body)));
      return Response.json({ attempt_id: "promotion-attempt", assessment: { outcome: "correct", feedback: "Correct" } });
    }
    return Response.json({ specification: { type: "move_line", prompt: "Promote on the board", mode: "single",
      accepted_lines: [[`a7a8${promotion}`]] } });
  }));
  render(<StudyExerciseRunner studyId="study" exerciseId="promotion-exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard />);
  await screen.findByText("Promote on the board");
  fireEvent.change(screen.getByLabelText("Promotion"), { target: { value: promotion } });
  act(() => useBoardShellStore.getState().board.onMove?.("a7", "a8"));
  const expectedBoard = new Chess(promotionFen);
  expectedBoard.move({ from: "a7", to: "a8", promotion });
  expect(useBoardShellStore.getState().board.fen).toBe(expectedBoard.fen());
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Correct");
  expect(submissions).toMatchObject([{ answer: { type: "move_line", moves: [`a7a8${promotion}`] } }]);
});

it("Study promotion resets to queen between exercises and is unavailable during board history", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => Response.json({ id: String(input).split("/").at(-2), revision: 1, type: "move_line", mode: "single",
    prompt: "Choose promotion", hint: "", fen: promotionFen })));
  const view = render(<StudyExerciseRunner studyId="study" exerciseId="first" boardTheme="brown" pieceSet="cburnett" useSharedBoard />);
  await screen.findByText("Choose promotion");
  fireEvent.change(screen.getByLabelText("Promotion"), { target: { value: "n" } });
  act(() => useBoardShellStore.getState().board.onMove?.("a7", "a8"));
  act(() => useBoardShellStore.getState().board.keyboard?.start?.());
  expect(screen.getByLabelText("Promotion").hasAttribute("disabled")).toBe(true);
  view.rerender(<StudyExerciseRunner studyId="study" exerciseId="second" boardTheme="brown" pieceSet="cburnett" useSharedBoard />);
  await screen.findByText("Choose promotion");
  await waitFor(() => expect((screen.getByLabelText("Promotion") as unknown as { value: string }).value).toBe("q"));
});
