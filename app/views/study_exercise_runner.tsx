"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess, type Square } from "chess.js";
import type { DrawShape } from "@lichess-org/chessground/draw";
import { Chessboard, type BoardTheme, type PieceSet } from "../components/chessboard";
import { Button } from "../components/buttons/BaseButton";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import type { PracticeCard } from "../domain/cards";
import { API_URL } from "../const";
import { availableKnightRoutes, evaluateStudyAnswer, studySpecificationSchema, type StudyAnswer } from "../domain/study-exercises";
import { recordOfflineStudyAttempt } from "../lib/offline-training";

type PresentedStudyExercise = {
  id: string; revision: number; type: "move_line" | "square_set" | "knight_path" | "choice" | "explanation";
  prompt: string; hint: string; fen: string; mode?: "single" | "stepwise_line";
  grading_policy?: "reference" | "open_judgment"; criterion?: string;
  candidate_region?: string[] | null; start_square?: string; target_squares?: string[];
  minimum_hops?: number; maximum_hops?: number; hop_rule?: "at_most" | "exact";
  occupancy_rule?: "static_non_capturing";
  options?: Array<{ id: string; text: string }>;
};

type AttemptReply = {
  attempt_id: string;
  assessment: { outcome: string; feedback: string; omitted?: string[]; extra?: string[] };
  pending_self_assessment?: boolean;
  rating?: string;
};

function responseError(status: number, detail?: string) {
  return detail ?? `Study service returned HTTP ${status}. Retry this attempt.`;
}

export default function StudyExerciseRunner({ studyId, exerciseId, card, boardTheme, pieceSet,
  useSharedBoard = false, onAdvance }: {
  studyId: string; exerciseId: string; card?: PracticeCard;
  boardTheme: BoardTheme; pieceSet: PieceSet; useSharedBoard?: boolean;
  onAdvance?: () => Promise<void>;
}) {
  const [exercise, setExercise] = useState<PresentedStudyExercise | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [selectedSquares, setSelectedSquares] = useState<string[]>([]);
  const [moveSequence, setMoveSequence] = useState<string[]>([]);
  const [coordinateInput, setCoordinateInput] = useState("");
  const [choiceIds, setChoiceIds] = useState<string[]>([]);
  const [reachable, setReachable] = useState(true);
  const [answerText, setAnswerText] = useState("");
  const [hintSeen, setHintSeen] = useState(false);
  const [reply, setReply] = useState<AttemptReply | null>(null);
  const [feedback, setFeedback] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const pendingRef = useRef<{ id: string; payload: Record<string, unknown> } | null>(null);
  const offlineAnswerRef = useRef<StudyAnswer | null>(null);
  const generationRef = useRef(0);
  const { setShellBoardForOwner, releaseShellBoardForOwner } = useBoardPublisher();

  useEffect(() => {
    const generation = ++generationRef.current;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (generation !== generationRef.current) return;
      setExercise(null); setLoadError(""); setSaveError(""); setSelectedSquares([]);
      setMoveSequence([]); setCoordinateInput(""); setChoiceIds([]); setReachable(true);
      setAnswerText(""); setHintSeen(false); setReply(null); setFeedback(null);
    });
    pendingRef.current = null;
    offlineAnswerRef.current = null;
    if (card?.studySnapshot) {
      const snapshot = card.studySnapshot;
      const specification = snapshot.specification;
      queueMicrotask(() => { if (generation !== generationRef.current) return; setExercise({ id: exerciseId, revision: snapshot.revision, type: specification.type,
        prompt: specification.prompt, hint: specification.hint, fen: snapshot.fen,
        ...("mode" in specification ? { mode: specification.mode, grading_policy: specification.grading_policy } : {}),
        ...("criterion" in specification ? { criterion: specification.criterion, candidate_region: specification.candidate_region } : {}),
        ...("start_square" in specification ? { start_square: specification.start_square,
          target_squares: specification.target_squares, minimum_hops: specification.minimum_hops,
          maximum_hops: specification.maximum_hops, hop_rule: specification.hop_rule,
          occupancy_rule: specification.occupancy_rule } : {}),
        ...("options" in specification ? { options: specification.options } : {}),
      }); });
      return () => { generationRef.current += 1; releaseShellBoardForOwner("train"); };
    }
    void fetch(`${API_URL}/api/studies/${studyId}/exercises/${exerciseId}/present`, { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json() as PresentedStudyExercise & { detail?: string };
        if (!response.ok) throw new Error(responseError(response.status, body.detail));
        if (generation === generationRef.current) setExercise(body);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && generation === generationRef.current)
          setLoadError(error instanceof Error ? error.message : "Could not load this exercise.");
      });
    return () => { controller.abort(); generationRef.current += 1; releaseShellBoardForOwner("train"); };
  }, [studyId, exerciseId, card?.studySnapshot, releaseShellBoardForOwner]);

  const answerLocked = Boolean(reply);
  const currentFen = useMemo(() => {
    if (!exercise || exercise.type !== "move_line") return exercise?.fen ?? card?.startingFen ?? "8/8/8/8/8/8/8/8 w - - 0 1";
    try {
      const board = new Chess(exercise.fen);
      for (const move of moveSequence) board.move(move);
      return board.fen();
    } catch { return exercise.fen; }
  }, [exercise, moveSequence, card?.startingFen]);

  const selectSquare = useCallback((square: Square) => {
    if (!exercise || answerLocked) return;
    if (exercise.type === "square_set")
      setSelectedSquares((current) => current.includes(square) ? current.filter((item) => item !== square) : [...current, square]);
    if (exercise.type === "knight_path")
      setSelectedSquares((current) => [...current, square].slice(0, 4));
  }, [exercise, answerLocked]);

  const onMove = useCallback((from: Square, to: Square) => {
    if (!exercise || exercise.type !== "move_line" || answerLocked) return;
    try {
      const board = new Chess(currentFen);
      const move = board.move({ from, to, promotion: "q" });
      if (move) setMoveSequence((current) => [...current, `${move.from}${move.to}${move.promotion ?? ""}`]);
    } catch { setSaveError("Choose a legal move, or enter promotion coordinates below."); }
  }, [exercise, answerLocked, currentFen]);

  const answerShapes = useMemo<DrawShape[]>(() => selectedSquares.map((square) => ({
    orig: square as Square, brush: "blue",
  })), [selectedSquares]);
  const selectionMode = exercise?.type === "square_set" || exercise?.type === "knight_path";
  useEffect(() => {
    if (!useSharedBoard || !exercise) return;
    setShellBoardForOwner("train", {
      fen: currentFen, orientation, interactionMode: reply ? "readonly" : selectionMode ? "select" : exercise.type === "move_line" ? "legal" : "readonly",
      showHint: false, theme: boardTheme, pieceSet, shapes: answerShapes, drawnShapes: [],
      lastMove: undefined, onMove, onSquareSelect: selectSquare,
    });
    return () => releaseShellBoardForOwner("train");
  }, [useSharedBoard, exercise, currentFen, orientation, reply, selectionMode, boardTheme, pieceSet,
    answerShapes, onMove, selectSquare, setShellBoardForOwner, releaseShellBoardForOwner]);

  const addCoordinates = () => {
    const values = coordinateInput.toLowerCase().match(/[a-h][1-8]/g) ?? [];
    if (exercise?.type === "square_set")
      setSelectedSquares((current) => [...new Set([...current, ...values])]);
    if (exercise?.type === "knight_path")
      setSelectedSquares(values.slice(0, 4));
    if (exercise?.type === "move_line") {
      try {
        const board = new Chess(currentFen);
        const move = board.move(coordinateInput.trim().toLowerCase());
        if (!move) throw new Error("Illegal move");
        setMoveSequence((current) => [...current, `${move.from}${move.to}${move.promotion ?? ""}`]);
      } catch { setSaveError("Enter a legal UCI move such as e7e8n."); return; }
    }
    setCoordinateInput(""); setSaveError("");
  };

  const makeAnswer = () => {
    if (!exercise) return null;
    if (exercise.type === "move_line") return { type: exercise.type, moves: moveSequence };
    if (exercise.type === "square_set") return { type: exercise.type, squares: selectedSquares };
    if (exercise.type === "knight_path") return { type: exercise.type, reachable, path: reachable ? selectedSquares : [] };
    if (exercise.type === "choice") return { type: exercise.type, option_ids: choiceIds,
      displayed_order: exercise.options?.map((option) => option.id) ?? [] };
    return { type: "explanation", text: answerText, ready: true };
  };

  const revealFeedback = async (attemptId: string, generation: number) => {
    const feedbackResponse = await fetch(`${API_URL}/api/studies/${studyId}/exercises/${exerciseId}/attempts/${attemptId}/feedback`);
    if (!feedbackResponse.ok) throw new Error("Answer saved, but feedback could not load. Retry feedback.");
    const revealed = await feedbackResponse.json() as { specification: Record<string, unknown> };
    if (generation === generationRef.current) { setFeedback(revealed.specification); setSaveError(""); }
  };

  const submit = async () => {
    if (!exercise || reply || busy) return;
    const generation = generationRef.current;
    if (card?.studySnapshot && card.queueEntryId) {
      try {
        const answer = makeAnswer() as StudyAnswer;
        const assessment = evaluateStudyAnswer(card.studySnapshot.specification, answer, card.studySnapshot.fen);
        if (assessment.outcome === "invalid_submission") { setSaveError(assessment.feedback); return; }
        if (assessment.outcome === "unrecognized" || assessment.outcome === "needs_self_assessment") {
          offlineAnswerRef.current = answer;
          setReply({ attempt_id: crypto.randomUUID(), assessment, pending_self_assessment: true });
          setFeedback(card.studySnapshot.specification as unknown as Record<string, unknown>);
          return;
        }
        await recordOfflineStudyAttempt(Number(card.queueEntryId), studyId, answer, undefined, hintSeen);
        if (generation === generationRef.current) {
          setReply({ attempt_id: crypto.randomUUID(), assessment, rating: assessment.outcome === "correct" ? "correct" : "again" });
          setFeedback(card.studySnapshot.specification as unknown as Record<string, unknown>);
        }
      } catch (error) { if (generation === generationRef.current) setSaveError(error instanceof Error ? error.message : "Could not save on phone."); }
      return;
    }
    const pending = pendingRef.current ?? (() => {
      const id = crypto.randomUUID();
      const payload: Record<string, unknown> = {
        attempt_id: id, revision: exercise.revision, answer: makeAnswer(),
        context: card ? "review" : "practice", hint_seen: hintSeen,
        solution_seen_before_answer: false,
      };
      if (card) {
        payload.card_id = card.backendId;
        payload.queue_entry_id = card.queueEntryId;
        payload.queue_cycle = card.queueCycle;
      }
      return { id, payload };
    })();
    pendingRef.current = pending;
    setBusy(true); setSaveError("");
    try {
      const response = await fetch(`${API_URL}/api/studies/${studyId}/exercises/${exerciseId}/attempts`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending.payload),
      });
      const body = await response.json() as AttemptReply & { detail?: string };
      if (!response.ok) throw new Error(responseError(response.status, body.detail));
      if (generation !== generationRef.current) return;
      setReply(body); pendingRef.current = null;
      await revealFeedback(pending.id, generation);
    } catch (error) { if (generation === generationRef.current) setSaveError(error instanceof Error ? error.message : "Could not save this attempt. Retry."); }
    finally { if (generation === generationRef.current) setBusy(false); }
  };

  const selfAssess = async (rating: "correct" | "again") => {
    if (!reply || !exercise || busy) return;
    const generation = generationRef.current;
    if (card?.studySnapshot && card.queueEntryId && offlineAnswerRef.current) {
      try {
        await recordOfflineStudyAttempt(Number(card.queueEntryId), studyId, offlineAnswerRef.current, rating, hintSeen);
        if (generation === generationRef.current) setReply({ ...reply, pending_self_assessment: false, rating });
        offlineAnswerRef.current = null;
      } catch (error) { if (generation === generationRef.current) setSaveError(error instanceof Error ? error.message : "Could not save self-assessment on phone."); }
      return;
    }
    setBusy(true); setSaveError("");
    try {
      const response = await fetch(`${API_URL}/api/studies/${studyId}/exercises/${exerciseId}/attempts/${reply.attempt_id}/self-assess`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rating }),
      });
      const body = await response.json() as AttemptReply & { detail?: string };
      if (!response.ok) throw new Error(responseError(response.status, body.detail));
      if (generation === generationRef.current) setReply(body);
    } catch (error) { if (generation === generationRef.current) setSaveError(error instanceof Error ? error.message : "Could not save self-assessment. Retry."); }
    finally { if (generation === generationRef.current) setBusy(false); }
  };

  const revealedSpecification = feedback ? studySpecificationSchema.safeParse(feedback) : null;
  let referenceAnswer = "";
  if (revealedSpecification?.success && exercise) {
    const rubric = revealedSpecification.data;
    if (rubric.type === "move_line") referenceAnswer = rubric.accepted_lines.map((line) => line.join(" ")).join("; ");
    if (rubric.type === "square_set") referenceAnswer = `Required: ${rubric.required.join(", ") || "none"}. Optional: ${rubric.optional.join(", ") || "none"}.`;
    if (rubric.type === "choice") referenceAnswer = rubric.options.filter((option) => rubric.correct_option_ids.includes(option.id)).map((option) => option.text).join("; ");
    if (rubric.type === "knight_path") {
      const routes = availableKnightRoutes(rubric, exercise.fen);
      referenceAnswer = routes.length ? `One valid route: ${routes[0].join(" → ")}` : "No route exists within the stated bound.";
    }
    if (rubric.type === "explanation") referenceAnswer = rubric.rubric;
  }

  if (loadError) return <div role="alert">{loadError} <Button onClick={() => window.location.reload()}>Retry</Button></div>;
  if (!exercise) return <p role="status">Loading study exercise…</p>;
  return <section className="study-exercise-runner" aria-label="Study exercise">
    <h2>Study exercise</h2>
    <p>{exercise.prompt}</p>
    {exercise.type === "square_set" && <p>{exercise.criterion}</p>}
    {exercise.type === "knight_path" && <p>Static, non-capturing knight route from {exercise.start_square} attacking {exercise.target_squares?.join(", ")}. {exercise.hop_rule === "exact" ? "Exactly" : "At most"} {exercise.maximum_hops} hops. Other pieces stay fixed.</p>}
    {!useSharedBoard && <Chessboard fen={currentFen} locked={Boolean(reply) || !selectionMode && exercise.type !== "move_line"}
      selectOnly={selectionMode && !reply} onMove={onMove} onSquareSelect={selectSquare}
      showHint={false} theme={boardTheme} pieceSet={pieceSet} shapes={answerShapes}
      orientation={orientation} onFlip={() => setOrientation((value) => value === "white" ? "black" : "white")} />}
    {exercise.type === "choice" && <fieldset disabled={answerLocked}><legend>Choose an answer</legend>
      {exercise.options?.map((option) => <label key={option.id}><input type="checkbox" checked={choiceIds.includes(option.id)}
        onChange={() => setChoiceIds((current) => current.includes(option.id) ? current.filter((id) => id !== option.id) : [...current, option.id])} />{option.text}</label>)}
    </fieldset>}
    {exercise.type === "explanation" && <label>Optional written answer<textarea disabled={answerLocked} value={answerText} onChange={(event) => setAnswerText(event.target.value)} /></label>}
    {exercise.type === "knight_path" && <label><input type="checkbox" checked={!reachable} disabled={answerLocked}
      onChange={(event) => { setReachable(!event.target.checked); setSelectedSquares([]); }} />No route within the stated bound</label>}
    {(selectionMode || exercise.type === "move_line") && <div>
      <p>Answer: {exercise.type === "move_line" ? moveSequence.join(" ") : selectedSquares.join(" → ") || "none"}</p>
      <label>Coordinates or UCI move<input value={coordinateInput} disabled={answerLocked} onChange={(event) => setCoordinateInput(event.target.value)} /></label>
      <Button disabled={answerLocked} onClick={addCoordinates}>Add</Button>
      <Button disabled={answerLocked} onClick={() => exercise.type === "move_line" ? setMoveSequence((items) => items.slice(0, -1)) : setSelectedSquares((items) => items.slice(0, -1))}>Undo</Button>
      <Button disabled={answerLocked} onClick={() => { setMoveSequence([]); setSelectedSquares([]); }}>Clear</Button>
    </div>}
    {!reply && exercise.hint && <Button onClick={() => setHintSeen(true)}>Hint</Button>}
    {hintSeen && !reply && <p>{exercise.hint}</p>}
    {!reply && <Button disabled={busy} onClick={() => void submit()}>{exercise.type === "explanation" ? "I have my answer" : "Submit"}</Button>}
    {saveError && <p role="alert">{saveError} {reply && !feedback
      ? <Button onClick={() => void revealFeedback(reply.attempt_id, generationRef.current).catch((error: unknown) => setSaveError(String(error)))}>Retry feedback</Button>
      : !reply && <Button onClick={() => void submit()}>Retry save</Button>}</p>}
    {reply && <div role="status"><p>{reply.assessment.feedback}</p>
      {reply.assessment.omitted?.length ? <p>Missing: {reply.assessment.omitted.join(", ")}</p> : null}
      {reply.assessment.extra?.length ? <p>Extra: {reply.assessment.extra.join(", ")}</p> : null}
      {feedback && <div><h3>Explanation</h3><p>{String(feedback.explanation ?? feedback.rubric ?? "")}</p>
        {referenceAnswer && <p><strong>Reference answer:</strong> {referenceAnswer}</p>}
        {Boolean(feedback.further_analysis) && <p>{String(feedback.further_analysis)}</p>}</div>}
      {reply.pending_self_assessment && <div><Button disabled={busy} onClick={() => void selfAssess("correct")}>Correct (self-assessed)</Button>
        <Button disabled={busy} onClick={() => void selfAssess("again")}>Again (self-assessed)</Button></div>}
      {!reply.pending_self_assessment && onAdvance && <Button onClick={() => void onAdvance()}>Continue</Button>}
    </div>}
  </section>;
}
