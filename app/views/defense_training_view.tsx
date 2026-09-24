"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Chess, type Square } from "chess.js";
import { Chessboard, type BoardTheme, type PieceSet } from "../components/chessboard";
import { BoardTools } from "../components/board-workspace";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { API_URL } from "../const";
import type { PracticeCard } from "../types";

type DefenseExercisePayload = {
  candidate_id: string;
  exercise_revision: number;
  card_id: string;
  prompt: string;
};

type DefenseGradePayload = {
  status: "correct" | "incorrect" | "needs_analysis" | "ambiguous" | "illegal";
  diagnostic: string;
  loss_cp?: number | null;
  allows_target_fork?: boolean;
  feedback?: {
    knight_route: Array<{ from_square: string; to_square: string }>;
    fork_geometry: {
      knight_to: string;
      king: { square: string };
      major: { square: string; piece: string };
    } | null;
    sound_moves: string[];
    refutation_uci: string[];
  };
};

type PendingAttempt = { attemptId: string; moveUci: string };

export default function DefenseTrainingView({
  card, boardTheme, pieceSet, useSharedBoard, onAdvance,
}: {
  card: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  useSharedBoard: boolean;
  onAdvance: () => Promise<void>;
}) {
  const candidateId = card.defenseCandidateId;
  const queueEntryId = card.queueEntryId;
  const [exercise, setExercise] = useState<DefenseExercisePayload | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [grade, setGrade] = useState<DefenseGradePayload | null>(null);
  const [pending, setPending] = useState<PendingAttempt | null>(null);
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef<PendingAttempt | null>(null);
  const submittingRef = useRef(false);
  const { setShellBoardForOwner, releaseShellBoardForOwner } = useBoardPublisher();

  const loadExercise = useCallback(async () => {
    if (!candidateId) {
      setLoadError("This defensive card has no candidate ID. Refresh the queue.");
      return;
    }
    try {
      const response = await fetch(`${API_URL}/api/defense-exercises/${candidateId}`);
      if (!response.ok) throw new Error(`Exercise unavailable (${response.status}). Refresh the queue.`);
      const body = await response.json() as DefenseExercisePayload;
      if (body.card_id !== card.backendId) throw new Error("Exercise and queue card differ. Refresh the queue.");
      setExercise(body);
      setLoadError("");
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : "Could not load the exercise.");
    }
  }, [candidateId, card.backendId]);

  useEffect(() => { queueMicrotask(() => void loadExercise()); }, [loadExercise]);

  const submit = useCallback(async (attempt: PendingAttempt) => {
    if (!candidateId || !queueEntryId || !exercise || submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    setSaveError("");
    try {
      const response = await fetch(`${API_URL}/api/defense-exercises/${candidateId}/attempt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          attempt_id: attempt.attemptId,
          exercise_revision: exercise.exercise_revision,
          queue_entry_id: queueEntryId,
          move_uci: attempt.moveUci,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `Could not grade the move (${response.status})`);
      }
      const result = await response.json() as DefenseGradePayload;
      setGrade(result);
      if (result.status !== "needs_analysis") {
        pendingRef.current = null;
        setPending(null);
      }
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Could not save this attempt. Retry it.");
    } finally {
      submittingRef.current = false;
      setBusy(false);
    }
  }, [candidateId, exercise, queueEntryId]);

  useEffect(() => {
    if (!pending || grade?.status !== "needs_analysis") return;
    const timer = window.setInterval(() => void submit(pending), 3_000);
    return () => window.clearInterval(timer);
  }, [pending, grade?.status, submit]);

  const onMove = useCallback((from: Square, to: Square) => {
    if (!exercise || busy || pendingRef.current || grade?.status === "correct" || grade?.status === "incorrect") return;
    try {
      const board = new Chess(card.startingFen);
      const move = board.move({ from, to, promotion: "q" });
      if (!move) return;
      const attempt = {
        attemptId: crypto.randomUUID(),
        moveUci: `${move.from}${move.to}${move.promotion ?? ""}`,
      };
      pendingRef.current = attempt;
      setPending(attempt);
      setGrade(null);
      void submit(attempt);
    } catch {
      setSaveError("That move is not legal from this position.");
    }
  }, [exercise, busy, grade?.status, card.startingFen, submit]);

  const locked = !exercise || busy || Boolean(pending) || grade?.status === "correct" || grade?.status === "incorrect";
  useEffect(() => {
    if (!useSharedBoard) return;
    setShellBoardForOwner("train", {
      unavailable: loadError || undefined,
      fen: card.startingFen,
      expectedSan: undefined,
      lastMove: undefined,
      interactionMode: locked ? "readonly" : "legal",
      showHint: false,
      theme: boardTheme,
      pieceSet,
      orientation: card.orientation === "black" ? "black" : "white",
      shapes: [],
      drawnShapes: [],
      positionRevision: card.revision ?? 1,
      onMove,
      onSquareSelect: undefined,
      onFreeMove: undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    });
    return () => releaseShellBoardForOwner("train");
  }, [useSharedBoard, setShellBoardForOwner, releaseShellBoardForOwner,
      card.startingFen, card.orientation, card.revision, loadError, locked,
      boardTheme, pieceSet, onMove]);

  const definitive = grade?.status === "correct" || grade?.status === "incorrect";
  return (
    <section className={`training-grid${useSharedBoard ? " training-grid-shared" : ""}`} aria-label="Defensive decision exercise">
      <div className="board-column">
        {!useSharedBoard && <Chessboard fen={card.startingFen} locked={Boolean(locked)} showHint={false}
          theme={boardTheme} pieceSet={pieceSet} orientation={card.orientation} onMove={onMove} />}
        <BoardTools>
          {saveError && pending && <button disabled={busy} onClick={() => void submit(pending)}>Retry move submission</button>}
          {loadError && <button onClick={() => void loadExercise()}>Retry loading exercise</button>}
        </BoardTools>
      </div>
      <div className="training-panel">
        <h2>Defensive decision</h2>
        {!definitive && <p>Choose your move.</p>}
        {loadError && <p role="alert">{loadError}</p>}
        {saveError && <p role="alert">{saveError}</p>}
        {grade?.status === "needs_analysis" && <p role="status">Analyzing this legal defense. Your study result has not been recorded yet.</p>}
        {grade?.status === "ambiguous" && <p role="status">This move is too close to the grading threshold. No review was recorded; choose another move.</p>}
        {grade?.status === "illegal" && <p role="alert">The submitted move is illegal. No review was recorded.</p>}
        {definitive && <>
          <p role="status">{grade.status === "correct" ? "Sound defense." : "This move loses value."}</p>
          {grade.feedback?.fork_geometry && <p>The knight reaches {grade.feedback.fork_geometry.knight_to}, checking the king on {grade.feedback.fork_geometry.king.square} and attacking the {grade.feedback.fork_geometry.major.piece} on {grade.feedback.fork_geometry.major.square}.</p>}
          {grade.feedback?.knight_route.length ? <p>Knight route: {grade.feedback.knight_route.map((hop) => `${hop.from_square}–${hop.to_square}`).join(", ")}.</p> : null}
          {grade.feedback?.sound_moves.length ? <p>Sound defensive ideas: {grade.feedback.sound_moves.join(", ")}.</p> : null}
          <button onClick={() => void onAdvance().catch(() => setSaveError("Result saved, but the next card could not load. Retry Continue."))}>Continue</button>
        </>}
      </div>
    </section>
  );
}
