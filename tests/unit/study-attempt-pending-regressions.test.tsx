import { studyIntentStore } from "../../database/browser/study-intent-storage";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import { asCardId, asFenString, asQueueEntryId, type PracticeCard } from "../../app/types";
import type { StudySpecification } from "../../app/domain/study-exercises";

vi.mock("@lichess-org/chessground", () => ({ Chessground: () => ({ set: vi.fn(), cancelMove: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), setAutoShapes: vi.fn(), setShapes: vi.fn() }) }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

it("F2 a cached Study specification never selects offline grading for a connected attempt", async () => {
  const fen = new Chess().fen();
  const specification: StudySpecification = { type: "move_line", prompt: "Connected cached exercise", hint: "", explanation: "Reference",
    grading_policy: "reference", mode: "single", accepted_lines: [["e2e4"]] };
  const card: PracticeCard = { id: asCardId("queue-1"), kind: "study", title: "Study", subtitle: "", startingFen: asFenString(fen), moves: [], userMoveTarget: 1,
    backendId: asCardId("card-a"), queueEntryId: asQueueEntryId(1), queueCycle: 0, revision: 1,
    studySnapshot: { schema_version: 1, grader_version: 1, exercise_id: "exercise", revision: 1, fen, specification } };
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input); requests.push(url);
    if (url.endsWith("/present")) return Response.json({ id: "exercise", revision: 1, type: "move_line", prompt: specification.prompt, hint: "", fen });
    if (url.endsWith("/attempts")) return Response.json({ attempt_id: JSON.parse(String(options?.body)).attempt_id,
      assessment: { outcome: "correct", feedback: "Saved by Learning" }, persisted: true });
    if (url.endsWith("/feedback")) return Response.json({ specification });
    throw new Error(`Unexpected request ${url}`);
  }));
  render(<StudyExerciseRunner studyId="study" exerciseId="exercise" card={card} boardTheme="brown" pieceSet="cburnett" />);
  await screen.findByText(specification.prompt);
  fireEvent.change(screen.getByLabelText("Coordinates or UCI move"), { target: { value: "e2e4" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Saved by Learning");
  expect(requests.some(url => url.endsWith("/attempts"))).toBe(true);
  const records = await (await studyIntentStore()).list();
  expect(records.filter(record => record.kind === "study-command" && record.state === "committed")).toHaveLength(1);
});

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
      return Response.json({ attempt_id: JSON.parse(String(init?.body)).attempt_id, assessment: { outcome: "incorrect", feedback: "Review the reference" }, rating: "again" });
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
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(false));
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
      return Response.json({ id: "exercise", revision: 3, type: "explanation",
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
  await waitFor(() => expect((screen.getByRole("button", { name: "I have my answer" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "I have my answer" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("still pending"));
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  await screen.findByRole("button", { name: "Correct (self-assessed)" });
  expect(attemptWrites).toHaveLength(1);
  expect(attemptStatusReads).toBe(2);
  expect(attemptWrites[0].key).toBe(attemptWrites[0].id);

  fireEvent.click(screen.getByRole("button", { name: "Correct (self-assessed)" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("still pending"));
  fireEvent.click(screen.getByRole("button", { name: "Again (self-assessed)" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Correct (self-assessed)" })).toBeNull());
  expect((await (await studyIntentStore()).list("study-command")).find(record => record.endpoint.endsWith("/self-assess"))?.revision).toBe(3);
  expect(assessmentStatusReads).toBe(2);
  expect(assessmentWrites).toEqual([
    { rating: "correct", key: `${attemptWrites[0].id}:self-assess` },
  ]);
});

it.each([false, true])("pr285_rejected_answer_can_be_corrected_with_fresh_identity (reload: %s)", async reload => {
  const { closeStudyIntentStorage, initializeStudyIntentStorage } = await import("../../database/browser/study-intent-storage");
  const submissions: Array<{ body: { attempt_id: string; answer: { moves: string[] } }; key: string }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/present")) return Response.json({ id: "exercise", revision: 1, type: "move_line", mode: "single", prompt: "Choose one legal move", hint: "", fen: new Chess().fen() });
    if (url.endsWith("/attempts")) {
      const body = JSON.parse(String(init?.body)); submissions.push({ body, key: (init?.headers as Record<string, string>)["Idempotency-Key"] });
      return body.answer.moves.length === 0 ? Response.json({ detail: "Submit one move" }, { status: 422 }) : Response.json({ attempt_id: body.attempt_id, assessment: { outcome: "correct", feedback: "Corrected answer saved" }, rating: "correct" });
    }
    if (url.endsWith("/feedback")) return Response.json({ specification: { type: "move_line", prompt: "Choose one legal move", mode: "single", grading_policy: "reference", accepted_lines: [["e2e4"]], explanation: "Develop" } });
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const runner = <StudyExerciseRunner studyId="repair" exerciseId="exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard />;
  let mounted = render(runner);
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Submit one move");
  const rejected = (await (await studyIntentStore()).list("study-command"))[0];
  expect(rejected).toMatchObject({ state: "discarded", payload: { body: { answer: { moves: [] } } } });
  if (reload) {
    mounted.unmount(); closeStudyIntentStorage(); await initializeStudyIntentStorage(); mounted = render(runner);
    await screen.findByText("Submit one move");
  }
  fireEvent.change(screen.getByLabelText("Coordinates or UCI move"), { target: { value: "e2e4" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Corrected answer saved");
  expect(submissions).toHaveLength(2);
  expect(submissions[0].key).toBe(submissions[0].body.attempt_id);
  expect(submissions[1].key).toBe(submissions[1].body.attempt_id);
  expect(submissions[1].key).not.toBe(submissions[0].key);
  expect(submissions[1].body.answer.moves).toEqual(["e2e4"]);
  const records = await (await studyIntentStore()).list("study-command");
  expect(records.map(record => record.state)).toEqual(["discarded", "committed"]);
  expect(records[0].payload).toEqual(rejected.payload);
  mounted.unmount();
});

it("pr285_practice_generation_new_self_assessment_cannot_reuse_the_old_attempt", async () => {
  const attempts: string[] = [], assessments: Array<{ parent: string; rating: string; key: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/present")) return Response.json({ id: "exercise", revision: 1, type: "explanation", prompt: "Explain the plan", hint: "", fen: new Chess().fen() });
    if (url.endsWith("/attempts")) { const body = JSON.parse(String(init?.body)); attempts.push(body.attempt_id); return Response.json({ attempt_id: body.attempt_id, assessment: { outcome: "needs_self_assessment", feedback: "Assess your answer" }, pending_self_assessment: true }); }
    if (url.endsWith("/self-assess")) {
      const parent = url.split("/").at(-2)!; const { rating } = JSON.parse(String(init?.body)); assessments.push({ parent, rating, key: (init?.headers as Record<string, string>)["Idempotency-Key"] });
      return Response.json({ attempt_id: parent, assessment: { outcome: "needs_self_assessment", feedback: "Assessment saved" }, pending_self_assessment: false, rating });
    }
    if (url.endsWith("/feedback")) return Response.json({ specification: { type: "explanation", prompt: "Explain the plan", rubric: "Develop pieces" } });
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(<StudyExerciseRunner studyId="practice" exerciseId="exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard />);
  for (const rating of ["Correct", "Again"]) {
    await waitFor(() => expect((screen.getByRole("button", { name: "I have my answer" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "I have my answer" }));
    await screen.findByText("Assess your answer");
    await waitFor(() => expect((screen.getByRole("button", { name: `${rating} (self-assessed)` }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: `${rating} (self-assessed)` }));
    await screen.findByText("Assessment saved");
    if (rating === "Correct") fireEvent.click(screen.getByRole("button", { name: "Practice again" }));
  }
  expect(attempts).toHaveLength(2); expect(attempts[1]).not.toBe(attempts[0]);
  expect(assessments.map(item => item.parent)).toEqual(attempts); expect(assessments.map(item => item.rating)).toEqual(["correct", "again"]);
  expect(assessments[1].key).not.toBe(assessments[0].key);
  expect(await (await studyIntentStore()).list("study-command")).toHaveLength(4);
});

it.each([false, true])("pr285_rejected_answer_self_assessment_replacement_uses_new_key (reload: %s)", async reload => {
  const { closeStudyIntentStorage, initializeStudyIntentStorage } = await import("../../database/browser/study-intent-storage");
  let parent = "", answerPosts = 0; const ratings: Array<{ key: string; rating: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/present")) return Response.json({ id: "exercise", revision: 1, type: "explanation", prompt: "Rate the plan", hint: "", fen: new Chess().fen() });
    if (url.endsWith("/attempts")) { answerPosts++; parent = JSON.parse(String(init?.body)).attempt_id; return Response.json({ attempt_id: parent, assessment: { outcome: "needs_self_assessment", feedback: "Rate it" }, pending_self_assessment: true }); }
    if (url.endsWith("/self-assess")) {
      const { rating } = JSON.parse(String(init?.body)); const key = (init?.headers as Record<string, string>)["Idempotency-Key"]; ratings.push({ rating, key });
      if (ratings.length === 1) return Response.json({ operation_id: key }, { status: 202 });
      return Response.json({ attempt_id: parent, assessment: { outcome: "needs_self_assessment", feedback: "Replacement assessment saved" }, pending_self_assessment: false, rating });
    }
    if (url.includes("/api/operations/")) return Response.json({ state: "failed", error: { status_code: 422, message: "Choose another assessment" } });
    if (url.endsWith("/feedback")) return Response.json({ specification: { type: "explanation", prompt: "Rate the plan", rubric: "Develop" } });
    throw new Error(`Unexpected request: ${url}`);
  }));
  const runner = <StudyExerciseRunner studyId="rating" exerciseId="exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard />;
  let mounted = render(runner);
  await waitFor(() => expect((screen.getByRole("button", { name: "I have my answer" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "I have my answer" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Correct (self-assessed)" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Correct (self-assessed)" })); await screen.findByText("Choose another assessment");
  if (reload) { mounted.unmount(); closeStudyIntentStorage(); await initializeStudyIntentStorage(); mounted = render(runner); await screen.findByText("Choose another assessment"); }
  await waitFor(() => expect((screen.getByRole("button", { name: "Again (self-assessed)" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Again (self-assessed)" })); await screen.findByText("Replacement assessment saved");
  expect(answerPosts).toBe(1); expect(ratings.map(item => item.rating)).toEqual(["correct", "again"]); expect(ratings[0].key).not.toBe(ratings[1].key);
  const records = await (await studyIntentStore()).list("study-command");
  expect(records.filter(record => record.attemptId !== parent)).toEqual([]);
  expect(records.find(record => record.state === "discarded")).toMatchObject({ requestId: ratings[0].key, payload: { body: { rating: "correct" } } });
  mounted.unmount();
});
