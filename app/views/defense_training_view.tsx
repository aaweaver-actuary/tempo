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
  rubric_version?: number;
  recognition_required?: boolean;
};

type DefenseGradePayload = {
  status: "correct" | "incorrect" | "needs_analysis" | "ambiguous" | "illegal";
  diagnostic: string;
  loss_cp?: number | null;
  allows_target_fork?: boolean;
  recognition_correct?: boolean | null;
  defense_status?: "correct" | "incorrect" | "needs_analysis" | "ambiguous" | "illegal" | "not_applicable";
  feedback?: {
    knight_route: Array<{ from_square: string; to_square: string }>;
    fork_geometry: {
      knight_to: string;
      king: { square: string };
      major: { square: string; piece: string };
    } | null;
    sound_moves: string[];
    refutation_uci: string[];
    control_explanation?: string;
    source_game_id?: string;
    source_game_url?: string | null;
  };
};

type PendingAttempt = { attemptId: string; moveUci: string };

function continuationNotation(startingFen: string, movesUci: string[]): string {
  try {
    const board = new Chess(startingFen);
    return movesUci.map((moveUci) => board.move({ from: moveUci.slice(0, 2),
      to: moveUci.slice(2, 4), promotion: moveUci[4] })?.san ?? moveUci).join(" ");
  } catch { return movesUci.join(" "); }
}

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
  const [selectedSquares, setSelectedSquares] = useState<string[]>([]);
  const [noConcreteThreat, setNoConcreteThreat] = useState(false);
  const [assessmentDone, setAssessmentDone] = useState(false);
  const [hintRevealed, setHintRevealed] = useState(false);
  const [consequence, setConsequence] = useState<"" | "checking_fork" | "other" | "none">("");
  const [recognitionDone, setRecognitionDone] = useState(false);
  const recognitionAttemptId = useRef<string | null>(null);
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
          recognition_attempt_id: recognitionAttemptId.current,
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
    if (!exercise || (exercise.recognition_required && !recognitionDone) || busy || pendingRef.current || grade?.status === "correct" || grade?.status === "incorrect") return;
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
  }, [exercise, recognitionDone, busy, grade?.status, card.startingFen, submit]);

  const selectSquare = useCallback((square: Square) => {
    if (recognitionDone || assessmentDone || busy) return;
    setSelectedSquares((current) => [...current.slice(0, 3), square]);
    setNoConcreteThreat(false);
  }, [recognitionDone, assessmentDone, busy]);

  const submitRecognition = async () => {
    if (!candidateId || !queueEntryId || !exercise || busy) return;
    setBusy(true);
    setSaveError("");
    const attemptId = recognitionAttemptId.current ?? crypto.randomUUID();
    recognitionAttemptId.current = attemptId;
    try {
      const response = await fetch(`${API_URL}/api/defense-exercises/${candidateId}/recognition`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          attempt_id: attemptId, exercise_revision: exercise.exercise_revision,
          queue_entry_id: queueEntryId, no_concrete_threat: noConcreteThreat,
          dangerous_piece_square: noConcreteThreat ? null : selectedSquares[0] ?? null,
          destination_square: noConcreteThreat ? null : selectedSquares[1] ?? null,
          king_square: noConcreteThreat ? null : selectedSquares[2] ?? null,
          major_square: noConcreteThreat ? null : selectedSquares[3] ?? null,
          consequence: noConcreteThreat ? "none" : consequence,
          hinted: hintRevealed,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `Could not save recognition answer (HTTP ${response.status})`);
      }
      const result = await response.json() as DefenseGradePayload;
      setRecognitionDone(true);
      if (result.status === "correct" || result.status === "incorrect") setGrade(result);
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Could not save recognition answer");
    } finally {
      setBusy(false);
    }
  };

  const recognitionStage = Boolean(exercise?.recognition_required && !recognitionDone);
  const locked = !exercise || busy || Boolean(pending) || recognitionStage || grade?.status === "correct" || grade?.status === "incorrect";
  useEffect(() => {
    if (!useSharedBoard) return;
    setShellBoardForOwner("train", {
      unavailable: loadError || undefined,
      fen: card.startingFen,
      expectedSan: undefined,
      lastMove: undefined,
      interactionMode: recognitionStage && !assessmentDone ? "free" : locked ? "readonly" : "legal",
      showHint: false,
      theme: boardTheme,
      pieceSet,
      orientation: card.orientation === "black" ? "black" : "white",
      shapes: [],
      drawnShapes: [],
      positionRevision: card.revision ?? 1,
      onMove,
      onSquareSelect: recognitionStage && !assessmentDone ? selectSquare : undefined,
      onFreeMove: recognitionStage && !assessmentDone ? (from, to) => { selectSquare(from); selectSquare(to); } : undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    });
    return () => releaseShellBoardForOwner("train");
  }, [useSharedBoard, setShellBoardForOwner, releaseShellBoardForOwner,
      card.startingFen, card.orientation, card.revision, loadError, locked,
      boardTheme, pieceSet, onMove, recognitionStage, assessmentDone, selectSquare]);

  const definitive = grade?.status === "correct" || grade?.status === "incorrect";
  return (
    <section className={`training-grid${useSharedBoard ? " training-grid-shared" : ""}`} aria-label="Defensive decision exercise">
      <div className="board-column">
        {!useSharedBoard && <Chessboard fen={card.startingFen} locked={Boolean(locked && (!recognitionStage || assessmentDone))} showHint={false}
          theme={boardTheme} pieceSet={pieceSet} orientation={card.orientation} onMove={onMove}
          editMode={recognitionStage && !assessmentDone} onSquareSelect={recognitionStage && !assessmentDone ? selectSquare : undefined}
          onFreeMove={recognitionStage && !assessmentDone ? (from, to) => { selectSquare(from); selectSquare(to); } : undefined} />}
        <BoardTools>
          {saveError && pending && <button disabled={busy} onClick={() => void submit(pending)}>Retry move submission</button>}
          {loadError && <button onClick={() => void loadExercise()}>Retry loading exercise</button>}
        </BoardTools>
      </div>
      <div className="training-panel">
        <h2>Defensive decision</h2>
        {!definitive && <p>{exercise?.prompt ?? "What danger should your next move account for?"}</p>}
        {recognitionStage && <div className="defense-recognition">
          {!assessmentDone ? <>
            <p>Assess the position. Select the dangerous piece, its destination, your king, and the threatened piece on the board. You can also type square names.</p>
            <div className="defense-recognition-squares">{["Dangerous piece", "Destination", "King", "Threatened piece"].map((label, index) => <label key={label}>{label}<input aria-label={label} maxLength={2} pattern="[a-h][1-8]" value={selectedSquares[index] ?? ""}
              onChange={(event) => { const value = event.target.value.toLowerCase(); setSelectedSquares((current) => { const next = [...current]; next[index] = value; return next; }); setNoConcreteThreat(false); }} /></label>)}</div>
            <label><input type="checkbox" checked={noConcreteThreat} onChange={(event) => setNoConcreteThreat(event.target.checked)} /> No concrete threat</label>
            <button type="button" onClick={() => setHintRevealed(true)}>Reveal hint</button>
            {hintRevealed && <p>Trace forcing moves and check whether the attacking piece can be captured. A revealed hint requires reinforcement.</p>}
            <button disabled={busy || (!noConcreteThreat && selectedSquares.filter((square) => /^[a-h][1-8]$/.test(square)).length !== 4)} onClick={() => setAssessmentDone(true)}>Explain danger</button>
          </> : <>
            <p>What would happen if you ignored the danger?</p>
            {!noConcreteThreat && <label>Consequence <select value={consequence} onChange={(event) => setConsequence(event.target.value as typeof consequence)}><option value="">Choose an explanation</option><option value="checking_fork">Check followed by material loss</option><option value="other">Material threat without check</option><option value="none">No forcing consequence</option></select></label>}
            {noConcreteThreat && <p>You found no concrete checking fork in the assessed route.</p>}
            <button disabled={busy || (!noConcreteThreat && !consequence)} onClick={() => void submitRecognition()}>Continue to move</button>
          </>}
        </div>}
        {recognitionDone && !definitive && <p>Now choose a move that addresses the position.</p>}
        {loadError && <p role="alert">{loadError}</p>}
        {saveError && <p role="alert">{saveError}</p>}
        {grade?.status === "needs_analysis" && <p role="status">Analyzing this legal defense. Your study result has not been recorded yet.</p>}
        {grade?.status === "ambiguous" && <p role="status">This move is too close to the grading threshold. No review was recorded; choose another move.</p>}
        {grade?.status === "illegal" && <p role="alert">The submitted move is illegal. No review was recorded.</p>}
        {definitive && <>
          {card.encounterBadges?.length ? <div className="card-meta" aria-label="Position encounter tags">{card.encounterBadges.map((badge) => <span key={badge} className="pill" title={`${card.encounterCount30d ?? 0} distinct games in 30 days${card.lastEncounteredAt ? ` · latest ${card.lastEncounteredAt.slice(0, 10)}` : ""}`}>{badge}</span>)}</div> : null}
          <p role="status">{grade.feedback?.control_explanation
            ? grade.status === "correct" ? "Correct: the apparent danger has a concrete refutation." : "The apparent danger can be refuted; review the capture."
            : grade.status === "correct" ? "Threat recognized and sound defense." : grade.defense_status === "correct" ? "Sound defense, but the danger needs another look." : "This move loses value."}</p>
          {grade.feedback?.fork_geometry && <p>The knight reaches {grade.feedback.fork_geometry.knight_to}, checking the king on {grade.feedback.fork_geometry.king.square} and attacking the {grade.feedback.fork_geometry.major.piece} on {grade.feedback.fork_geometry.major.square}.</p>}
          {grade.feedback?.control_explanation && <p>{grade.feedback.control_explanation}</p>}
          {grade.feedback?.knight_route.length ? <p>Knight route: {grade.feedback.knight_route.map((hop) => `${hop.from_square}–${hop.to_square}`).join(", ")}.</p> : null}
          {grade.feedback?.sound_moves.length ? <p>Sound defensive ideas: {grade.feedback.sound_moves.join(", ")}.</p> : null}
          {grade.feedback?.refutation_uci.length ? <p>Continuation: {continuationNotation(card.startingFen, grade.feedback.refutation_uci)}</p> : null}
          {grade.feedback?.source_game_id && <p>Source game: {grade.feedback.source_game_url
            ? <a href={grade.feedback.source_game_url} target="_blank" rel="noreferrer">{grade.feedback.source_game_id}</a>
            : grade.feedback.source_game_id}</p>}
          <button onClick={() => void onAdvance().catch(() => setSaveError("Result saved, but the next card could not load. Retry Continue."))}>Continue</button>
        </>}
      </div>
    </section>
  );
}
