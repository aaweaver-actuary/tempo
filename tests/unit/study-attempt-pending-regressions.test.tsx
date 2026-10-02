import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";

vi.mock("@lichess-org/chessground", () => ({ Chessground: () => ({ set: vi.fn(), cancelMove: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), setAutoShapes: vi.fn(), setShapes: vi.fn() }) }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

it.each([false, true])("study feedback browses the reference line and R restores an off-line submitted answer (shared board: %s)", async (useSharedBoard) => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{ width: 400 }] as unknown as DOMRectList);
  const startingFen = new Chess().fen();
  const submittedBoard = new Chess(); submittedBoard.move("d4"); submittedBoard.move("d5");
  const referenceBoard = new Chess(); referenceBoard.move("e4"); referenceBoard.move("e5");
  const submissions: unknown[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/present")) return Response.json({ id: "exercise", revision: 1, type: "move_line",
      prompt: "Find the reference line", hint: "Develop a piece", fen: startingFen });
    if (url.endsWith("/attempts") && init?.method === "POST") {
      submissions.push(JSON.parse(String(init.body)));
      return Response.json({ attempt_id: "submitted-attempt", assessment: { outcome: "incorrect", feedback: "Review the reference" }, rating: "again" });
    }
    if (url.endsWith("/feedback")) return Response.json({ specification: { type: "move_line", prompt: "Find the reference line",
      hint: "Develop a piece", explanation: "Reference continuation", grading_policy: "reference", mode: "stepwise_line",
      accepted_lines: [["e2e4", "e7e5"]] } });
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const advance = vi.fn(async () => undefined);
  render(<>{useSharedBoard && <PersistentBoardShell />}<StudyExerciseRunner studyId="study" exerciseId="exercise"
    boardTheme="brown" pieceSet="cburnett" useSharedBoard={useSharedBoard} onAdvance={advance} /></>);
  await screen.findByText("Find the reference line");
  for (const move of ["d2d4", "d7d5"]) {
    fireEvent.change(screen.getByLabelText("Coordinates or UCI move"), { target: { value: move } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
  }
  fireEvent.click(screen.getByRole("button", { name: "Hint" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Reference continuation");
  const frame = document.querySelector(".board-frame")!;
  expect(frame.getAttribute("data-fen")).toBe(submittedBoard.fen());
  expect(submissions).toHaveLength(1);
  const savedSubmission = structuredClone(submissions[0]);
  expect(savedSubmission).toMatchObject({ answer: { type: "move_line", moves: ["d2d4", "d7d5"] }, hint_seen: true });

  fireEvent.keyDown(window, { key: "Home" });
  expect(frame.getAttribute("data-fen")).toBe(startingFen);
  expect(frame.getAttribute("data-input-enabled")).toBe("false");
  fireEvent.keyDown(window, { key: "End" });
  expect(frame.getAttribute("data-fen")).toBe(referenceBoard.fen());
  expect(frame.getAttribute("data-input-enabled")).toBe("false");
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(frame.getAttribute("data-fen")).toBe(referenceBoard.fen());
  fireEvent.keyDown(window, { key: "r" });
  expect(frame.getAttribute("data-fen")).toBe(submittedBoard.fen());
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(frame.getAttribute("data-fen")).toBe(submittedBoard.fen());
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  expect(frame.getAttribute("data-fen")).toBe(referenceBoard.fen());
  expect(frame.getAttribute("data-input-enabled")).toBe("false");
  fireEvent.keyDown(window, { key: "r" });
  expect(frame.getAttribute("data-fen")).toBe(submittedBoard.fen());
  expect(screen.getByText("Answer: d2d4 d7d5")).toBeTruthy();
  expect(screen.getByText("Review the reference")).toBeTruthy();
  expect(submissions).toEqual([savedSubmission]);
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(advance).not.toHaveBeenCalled();
});

it("study shortcuts preserve submitted moves and hints without revealing or submitting an unanswered line", async () => {
  const fetcher = vi.fn(async () => Response.json({ id: "exercise", revision: 1, type: "move_line",
    prompt: "Find the continuation", hint: "Develop a piece", fen: new Chess().fen() }));
  vi.stubGlobal("fetch", fetcher);
  const advance = vi.fn(async () => undefined);
  render(<StudyExerciseRunner studyId="study" exerciseId="exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard onAdvance={advance} />);
  await screen.findByText("Find the continuation");
  const original = useBoardShellStore.getState().board;
  act(() => original.keyboard?.end?.());
  expect(useBoardShellStore.getState().board.fen).toBe(new Chess().fen());
  expect(useBoardShellStore.getState().board.keyboard?.nextItem).toBeUndefined();
  act(() => useBoardShellStore.getState().board.onMove?.("e2", "e4"));
  const played = new Chess(); played.move("e4");
  act(() => useBoardShellStore.getState().board.keyboard?.hint?.());
  expect(screen.getByText("Develop a piece")).toBeTruthy();
  act(() => useBoardShellStore.getState().board.keyboard?.start?.());
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  act(() => useBoardShellStore.getState().board.keyboard?.reset?.());
  expect(useBoardShellStore.getState().board.fen).toBe(played.fen());
  expect(screen.getByText("Develop a piece")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledOnce(); expect(advance).not.toHaveBeenCalled();
});

it("Study attempt and self-assessment retry pending Celery operations with the same IDs", async () => {
  const attemptWrites: Array<{ id: string; key: string }> = [];
  const assessmentWrites: Array<{ rating: string; key: string }> = [];
  let attemptStatusReads = 0;
  let assessmentStatusReads = 0;

  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/present"))
      return Response.json({ id: "exercise", revision: 1, type: "explanation",
        prompt: "Explain the plan", hint: "", fen: new Chess().fen() });
    if (url.endsWith("/attempts") && init?.method === "POST") {
      const attempt = JSON.parse(String(init.body)) as { attempt_id: string };
      attemptWrites.push({ id: attempt.attempt_id,
        key: String((init.headers as Record<string, string>)["Idempotency-Key"]) });
      return Response.json({ operation_id: attempt.attempt_id, state: "pending" }, { status: 202 });
    }
    if (url.includes("/self-assess") && init?.method === "POST") {
      const assessment = JSON.parse(String(init.body)) as { rating: string };
      assessmentWrites.push({ rating: assessment.rating,
        key: String((init.headers as Record<string, string>)["Idempotency-Key"]) });
      return Response.json({ operation_id: assessmentWrites.at(-1)?.key, state: "pending" },
        { status: 202 });
    }
    if (url.includes("/api/operations/") && decodeURIComponent(url).endsWith(":self-assess")) {
      assessmentStatusReads += 1;
      return Response.json(assessmentStatusReads === 1 ? { state: "pending" } : {
        state: "complete", response: { attempt_id: attemptWrites[0].id,
          assessment: { outcome: "needs_self_assessment", feedback: "Assess your answer" },
          rating: "correct", pending_self_assessment: false },
      });
    }
    if (url.includes("/api/operations/")) {
      attemptStatusReads += 1;
      return Response.json(attemptStatusReads === 1 ? { state: "pending" } : {
        state: "complete", response: { attempt_id: attemptWrites[0].id,
          assessment: { outcome: "needs_self_assessment", feedback: "Assess your answer" },
          pending_self_assessment: true },
      });
    }
    if (url.endsWith("/feedback"))
      return Response.json({ specification: { type: "explanation", prompt: "Explain the plan",
        rubric: "Develop pieces" } });
    throw new Error(`Unexpected request: ${url}`);
  }));

  render(<StudyExerciseRunner studyId="study" exerciseId="exercise"
    boardTheme="brown" pieceSet="cburnett" useSharedBoard />);
  fireEvent.click(await screen.findByRole("button", { name: "I have my answer" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("still pending"));
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  await screen.findByRole("button", { name: "Correct (self-assessed)" });
  expect(attemptWrites).toHaveLength(2);
  expect(attemptWrites[0]).toEqual(attemptWrites[1]);
  expect(attemptWrites[0].key).toBe(attemptWrites[0].id);

  fireEvent.click(screen.getByRole("button", { name: "Correct (self-assessed)" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("still pending"));
  fireEvent.click(screen.getByRole("button", { name: "Correct (self-assessed)" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Correct (self-assessed)" })).toBeNull());
  expect(assessmentWrites).toEqual([
    { rating: "correct", key: `${attemptWrites[0].id}:self-assess` },
    { rating: "correct", key: `${attemptWrites[0].id}:self-assess` },
  ]);
});
