import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";

afterEach(() => vi.unstubAllGlobals());

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
