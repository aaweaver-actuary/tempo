import { describe, expect, it } from "vitest";
import { classifyFrozenWork, decodeFrozenAttempt, decodeFrozenFeedback, decodeFrozenPresentation } from "../../app/domain/study-exercise-transport";

const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const presented = { id: "exercise", revision: 3, type: "move_line", prompt: "Move", hint: "", fen, mode: "single", grading_policy: "reference" };
const assessment = { outcome: "needs_self_assessment", feedback: "Compare" };
const pending = { attempt_id: "attempt", assessment, pending_self_assessment: true };
const completed = { attempt_id: "attempt", assessment: { outcome: "correct", feedback: "Good" }, rating: "correct", requested_rating: "correct", persisted: true, review: { review_id: 42, requeue_entry_id: null } };

describe("retained frozen exercise transport", () => {
  it("validates identity, revision and learner-only projection", () => {
    expect(decodeFrozenPresentation(presented, { exerciseId: "exercise", revision: 3 })).toEqual(presented);
    for (const value of [{...presented, id: "other"}, {...presented, revision: 4}, {...presented, fen: "bad"}, {...presented, accepted_lines: [["e2e4"]]}, {...presented, prompt: ""}])
      expect(() => decodeFrozenPresentation(value, { exerciseId: "exercise", revision: 3 })).toThrow();
  });
  it("distinguishes a completed parent awaiting assessment from a persisted human review", () => {
    expect(decodeFrozenAttempt(pending, { attemptId: "attempt", context: "review" })).toEqual(pending);
    expect(decodeFrozenAttempt(completed, { attemptId: "attempt", context: "review" })).toEqual(completed);
    expect(decodeFrozenAttempt({...completed, review: null}, { attemptId: "attempt", context: "practice" }).review).toBeNull();
    for (const value of [{...pending, attempt_id: "foreign"}, {...pending, assessment: { ...assessment, outcome: "correct" }}, {...completed, persisted: false}, {...completed, review: null}, { attempt_id: "attempt" }])
      expect(() => decodeFrozenAttempt(value, { attemptId: "attempt", context: "review" })).toThrow();
  });
  it("validates feedback shape and rejects malformed 200 projections", () => {
    const specification = { type: "explanation", prompt: "Why?", hint: "", explanation: "", rubric: "Compare" };
    expect(decodeFrozenFeedback({ specification }).specification).toEqual(specification);
    expect(() => decodeFrozenFeedback({ specification: { ...specification, type: "unknown" } })).toThrow();
    expect(() => decodeFrozenFeedback({ specification: { ...specification, rubric: "x".repeat(10001) } })).toThrow();
    expect(() => decodeFrozenFeedback({ detail: "unavailable" })).toThrow();
  });
  it("retains an owner-valid historical reference longer than the answer limit", () => {
    const line = Array.from({ length: 11 }, () => ["g1f3", "g8f6", "f3g1", "f6g8"]).flat();
    const specification = { type: "move_line", prompt: "Continue", hint: "", explanation: "",
      mode: "stepwise_line", grading_policy: "reference", accepted_lines: [line] };
    expect(decodeFrozenFeedback({ specification }).specification).toEqual(specification);
  });
  it("uses the command owner's attempt identity limit and exact original ID", () => {
    for (const attemptId of ["", "a".repeat(100), "🐎".repeat(100)])
      expect(decodeFrozenAttempt({ ...pending, attempt_id: attemptId }, { attemptId, context: "review" }).attempt_id).toBe(attemptId);
    for (const attemptId of ["a".repeat(101), "🐎".repeat(101)])
      expect(() => decodeFrozenAttempt({ ...pending, attempt_id: attemptId }, { attemptId, context: "review" })).toThrow();
  });
  it("counts owner text limits in Unicode characters", () => {
    expect(decodeFrozenPresentation({ ...presented, prompt: "🐎".repeat(2000) }, { exerciseId: "exercise" }).prompt).toHaveLength(4000);
    expect(() => decodeFrozenPresentation({ ...presented, prompt: "🐎".repeat(2001) }, { exerciseId: "exercise" })).toThrow();
    expect(decodeFrozenFeedback({ specification: { type: "explanation", prompt: "Why?", hint: "", explanation: "", rubric: "🐎".repeat(10000) } }).specification.type).toBe("explanation");
  });
  it("supports only positively identified frozen work or original recovery", () => {
    expect(classifyFrozenWork({authorityKnown: true, kind: "study_exercise", linkedPublished: true})).toBe("supported");
    expect(classifyFrozenWork({authorityKnown: true, kind: "study_exercise", originalRecovery: true})).toBe("recovery");
    for (const facts of [{kind: "study_exercise", linkedPublished: true}, {authorityKnown: true, kind: "defense", originalRecovery: true}, {authorityKnown: true, kind: "study_exercise", ledgerRetired: true}, {authorityKnown: true, kind: "study-command"}, {authorityKnown: true, kind: "study_exercise", linkedPublished: true, admissionSource: "endgame:old"}])
      expect(classifyFrozenWork(facts)).toBe("held");
  });
});
