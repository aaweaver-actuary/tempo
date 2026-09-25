"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess, type Square } from "chess.js";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
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
  proposed_move_uci?: string;
  proposed_move_san?: string;
  preview_fen?: string;
  fork_move_san?: string;
};

type DefenseGradePayload = {
  status: "correct" | "incorrect" | "needs_analysis" | "ambiguous" | "illegal" | "ready_for_move";
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
  card, boardTheme, pieceSet, useSharedBoard, onAdvance, onBury = async () => undefined,
}: {
  card: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  useSharedBoard: boolean;
  onAdvance: () => Promise<void>;
  onBury?: () => Promise<void>;
}) {
  const candidateId = card.defenseCandidateId;
  const queueEntryId = card.queueEntryId;
  const [exercise, setExercise] = useState<DefenseExercisePayload | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [burying, setBurying] = useState(false);
  const [buryError, setBuryError] = useState("");
  const [grade, setGrade] = useState<DefenseGradePayload | null>(null);
  const [pending, setPending] = useState<PendingAttempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedSquares, setSelectedSquares] = useState<string[]>([]);
  const [activeSelection, setActiveSelection] = useState(0);
  const [squareInput, setSquareInput] = useState("");
  const [recognitionResult, setRecognitionResult] = useState<DefenseGradePayload | null>(null);
  const [noConcreteThreat, setNoConcreteThreat] = useState(false);
  const [assessmentDone, setAssessmentDone] = useState(false);
  const [hintRevealed, setHintRevealed] = useState(false);
  const [consequence, setConsequence] = useState<"" | "checking_fork" | "other" | "none">("");
  const [recognitionDone, setRecognitionDone] = useState(false);
  const [defenseReady, setDefenseReady] = useState(false);
  const recognitionAttemptId = useRef<string | null>(null);
  const loadedRevision = useRef<number | null>(null);
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
      if (body.rubric_version !== 3 || !body.preview_fen || !body.proposed_move_san) {
        throw new Error("This exercise needs a refreshed board preview. Retry loading the exercise.");
      }
      if (loadedRevision.current !== null && loadedRevision.current !== body.exercise_revision) {
        setGrade(null); setRecognitionResult(null); setRecognitionDone(false); setDefenseReady(false);
        setSelectedSquares([]); setActiveSelection(0); setAssessmentDone(false);
        setNoConcreteThreat(false); setConsequence(""); setHintRevealed(false);
        recognitionAttemptId.current = null; pendingRef.current = null; setPending(null);
      }
      loadedRevision.current = body.exercise_revision;
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
    if (!exercise || (exercise.recognition_required && !defenseReady) || busy || pendingRef.current || grade?.status === "correct" || grade?.status === "incorrect") return;
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
  }, [exercise, defenseReady, busy, grade?.status, card.startingFen, submit]);

  const selectSquare = useCallback((square: Square) => {
    if (recognitionDone || assessmentDone || busy || activeSelection > 3) return;
    setSelectedSquares((current) => [...current.slice(0, activeSelection), square]);
    setSquareInput("");
    if (activeSelection === 3) setAssessmentDone(true);
    else setActiveSelection((current) => current + 1);
    setNoConcreteThreat(false);
  }, [recognitionDone, assessmentDone, busy, activeSelection]);

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
          rubric_version: exercise.rubric_version,
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
      setRecognitionResult(result);
      if (result.status === "correct" || result.status === "incorrect") setGrade(result);
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Could not save recognition answer");
    } finally {
      setBusy(false);
    }
  };

  const recognitionStage = Boolean(exercise?.recognition_required && !recognitionDone);
  const showingPreview = Boolean(exercise?.preview_fen && !defenseReady);
  const boardFen = showingPreview ? exercise!.preview_fen! : card.startingFen;
  const originalMoveNumber = Number(card.startingFen.split(" ")[5] ?? 1);
  const proposedMoveLabel = exercise?.proposed_move_san
    ? `${originalMoveNumber}${card.startingFen.split(" ")[1] === "b" ? "..." : "."}${exercise.proposed_move_san}` : "";
  const selectionLabels = ["Dangerous piece", "Destination", "Your king", "Threatened piece"];
  const selectionPrompts = [
    "Select the piece that could create the danger.",
    "Where could that piece move to create the danger?",
    "Which king would be threatened?",
    "Which other piece would be threatened?",
  ];
  const shownFeedback = grade?.feedback ?? recognitionResult?.feedback;
  const recognitionShapes: DrawShape[] = useMemo(() => recognitionDone && showingPreview && shownFeedback
    ? [
        ...shownFeedback.knight_route.map((hop) => ({ orig: hop.from_square as Key, dest: hop.to_square as Key, brush: "red" })),
        ...(shownFeedback.fork_geometry ? [
          { orig: shownFeedback.fork_geometry.knight_to as Key, dest: shownFeedback.fork_geometry.king.square as Key, brush: "red" },
          { orig: shownFeedback.fork_geometry.knight_to as Key, dest: shownFeedback.fork_geometry.major.square as Key, brush: "red" },
        ] : []),
      ]
    : selectedSquares.flatMap((square, index) => {
        if (!/^[a-h][1-8]$/.test(square)) return [];
        if (index === 1 && /^[a-h][1-8]$/.test(selectedSquares[0] ?? ""))
          return [{ orig: selectedSquares[0] as Key, dest: square as Key, brush: "blue" }];
        return [{ orig: square as Key, brush: "blue" }];
      }), [recognitionDone, showingPreview, shownFeedback, selectedSquares]);
  const locked = !exercise || busy || Boolean(pending) || recognitionStage
    || (recognitionDone && !defenseReady) || grade?.status === "correct" || grade?.status === "incorrect";
  useEffect(() => {
    if (!useSharedBoard) return;
    setShellBoardForOwner("train", {
      unavailable: loadError || undefined,
      fen: boardFen,
      expectedSan: undefined,
      lastMove: undefined,
      interactionMode: recognitionStage && !assessmentDone ? "free" : locked ? "readonly" : "legal",
      showHint: false,
      theme: boardTheme,
      pieceSet,
      orientation: card.orientation === "black" ? "black" : "white",
      shapes: recognitionShapes,
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
      boardFen, card.orientation, card.revision, loadError, locked,
      boardTheme, pieceSet, onMove, recognitionStage, assessmentDone, selectSquare, recognitionShapes]);

  const definitive = grade?.status === "correct" || grade?.status === "incorrect";
  return (
    <section className={`training-grid${useSharedBoard ? " training-grid-shared" : ""}`} aria-label="Defensive decision exercise">
      <div className="board-column">
        {!useSharedBoard && <Chessboard fen={boardFen} locked={Boolean(locked && (!recognitionStage || assessmentDone))} showHint={false}
          theme={boardTheme} pieceSet={pieceSet} orientation={card.orientation} onMove={onMove} shapes={recognitionShapes}
          editMode={recognitionStage && !assessmentDone} onSquareSelect={recognitionStage && !assessmentDone ? selectSquare : undefined}
          onFreeMove={recognitionStage && !assessmentDone ? (from, to) => { selectSquare(from); selectSquare(to); } : undefined} />}
        <BoardTools>
          <button type="button" disabled={burying || busy || Boolean(definitive)} onClick={() => void runBury()}>{burying ? "Burying…" : "Bury"}</button>
          {saveError && pending && <button disabled={busy} onClick={() => void submit(pending)}>Retry move submission</button>}
          {loadError && <button onClick={() => void loadExercise()}>Retry loading exercise</button>}
        </BoardTools>
      </div>
      <aside className="study-panel defense-study-panel">
        <p className="side-to-play">{showingPreview ? `Preview after ${proposedMoveLabel} · ${card.orientation === "white" ? "Black" : "White"} to play` : `${card.orientation} to play`}</p>
        <div className="card-meta"><span className="pill">Defensive decision</span>
          {card.queueAttemptState === "reinforcement" && <span className="pill">Reinforcement</span>}
        </div>
        <div className="opening-title"><p>Recognize, explain, respond</p><h2>What danger should your next move account for?</h2>
          <span>{recognitionDone ? definitive ? "Review" : defenseReady ? "Choose a move" : "Review the proposed move" : assessmentDone ? "Explain" : `Assess · step ${activeSelection + 1} of 4`}</span></div>
        {showingPreview && <p className="defense-preview-label">Consider <strong>{proposedMoveLabel}</strong>. This board shows the position after that move.</p>}
        {recognitionStage && <div className="defense-recognition">
          {!assessmentDone ? <>
            <p className="defense-stage-prompt">{selectionPrompts[activeSelection]}</p>
            <ol className="defense-selection-trail">{selectedSquares.map((square, index) =>
              <li key={index}><span>{selectionLabels[index]}: <strong>{square}</strong></span>
                <button type="button" onClick={() => { setSelectedSquares((current) => current.slice(0, index)); setActiveSelection(index); setSquareInput(""); }}
                  aria-label={`Change ${selectionLabels[index].toLowerCase()}`}>Change</button></li>)}</ol>
            <label className="defense-square-entry">{selectionLabels[activeSelection]} square
              <input aria-label={`${selectionLabels[activeSelection]} square`} inputMode="text" autoComplete="off" maxLength={2}
                value={squareInput} onChange={(event) => setSquareInput(event.target.value.toLowerCase())}
                onKeyDown={(event) => { if (event.key === "Enter" && /^[a-h][1-8]$/.test(squareInput)) { event.preventDefault(); selectSquare(squareInput as Square); } }} />
              <button type="button" disabled={!/^[a-h][1-8]$/.test(squareInput)} onClick={() => selectSquare(squareInput as Square)}>Select</button>
            </label>
            <button type="button" className="defense-no-threat" onClick={() => { setNoConcreteThreat(true); setSelectedSquares([]); setConsequence("none"); setAssessmentDone(true); }}>No concrete threat</button>
            <button type="button" onClick={() => setHintRevealed(true)}>Hint</button>
            {hintRevealed && <p>Trace forcing moves and check whether the attacking piece can be captured. A revealed hint requires reinforcement.</p>}
          </> : <>
            <p className="defense-stage-prompt">{noConcreteThreat ? "You found no concrete checking fork. Submit that assessment." : "What would happen if you ignored the danger?"}</p>
            {!noConcreteThreat && <label>Consequence <select value={consequence} onChange={(event) => setConsequence(event.target.value as typeof consequence)}><option value="">Choose an explanation</option><option value="checking_fork">Check followed by material loss</option><option value="other">Material threat without check</option><option value="none">No forcing consequence</option></select></label>}
            <div className="defense-stage-actions"><button type="button" onClick={() => { setAssessmentDone(false); setActiveSelection(noConcreteThreat ? 0 : 3); setNoConcreteThreat(false); }}>Back to board</button>
              <button disabled={busy || (!noConcreteThreat && !consequence)} onClick={() => void submitRecognition()}>Submit assessment</button></div>
          </>}
        </div>}
        {recognitionDone && <div className={`feedback ${recognitionResult?.recognition_correct ? "complete" : "wrong"}`} role="status">
          <span className="feedback-icon">{recognitionResult?.recognition_correct ? "✓" : "!"}</span><div>
            <strong>{recognitionResult?.recognition_correct ? "Danger assessed" : "Review the danger"}</strong>
            {recognitionResult?.feedback?.control_explanation ? <p>{recognitionResult.feedback.control_explanation}</p>
              : recognitionResult?.feedback?.fork_geometry ? <p>After {proposedMoveLabel}, {exercise?.fork_move_san ?? "the knight move"} checks the king on {recognitionResult.feedback.fork_geometry.king.square} and attacks the {recognitionResult.feedback.fork_geometry.major.piece} on {recognitionResult.feedback.fork_geometry.major.square}. The verified line continues {continuationNotation(card.startingFen, recognitionResult.feedback.refutation_uci.slice(0, 4))}.</p> : null}
          </div></div>}
        {recognitionDone && !definitive && !defenseReady && <button type="button" onClick={() => setDefenseReady(true)}>Continue to defense</button>}
        {recognitionDone && !definitive && defenseReady && <p className="defense-stage-prompt">Back at your original turn, play a move that avoids this danger. More than one sound defense may work.</p>}
        {loadError && <p role="alert">{loadError}</p>}
        {saveError && <p role="alert">{saveError} {/reload|refresh/i.test(saveError) && <button type="button" onClick={() => void loadExercise()}>Reload exercise</button>}</p>}
        {buryError && <p role="alert">{buryError} <button type="button" onClick={() => void runBury()}>Retry bury</button></p>}
        {grade?.status === "needs_analysis" && <p role="status">Analyzing this legal defense. Your study result has not been recorded yet.</p>}
        {grade?.status === "ambiguous" && <p role="status">This move is too close to the grading threshold. No review was recorded; choose another move.</p>}
        {grade?.status === "illegal" && <p role="alert">The submitted move is illegal. No review was recorded.</p>}
        {definitive && <>
          {card.encounterBadges?.length ? <div className="card-meta" aria-label="Position encounter tags">{card.encounterBadges.map((badge) => <span key={badge} className="pill" title={`${card.encounterCount30d ?? 0} distinct games in 30 days${card.lastEncounteredAt ? ` · latest ${card.lastEncounteredAt.slice(0, 10)}` : ""}`}>{badge}</span>)}</div> : null}
          <p role="status">{grade.feedback?.control_explanation
            ? grade.status === "correct" ? "Correct: the apparent danger has a concrete refutation." : "The apparent danger can be refuted; review the capture."
            : grade.status === "correct" ? "Threat recognized and sound defense." : grade.defense_status === "correct" ? "Sound defense, but the danger needs another look." : "This move loses value."}</p>
          {grade.feedback?.fork_geometry && <p>After the proposed {proposedMoveLabel}, the knight reaches {grade.feedback.fork_geometry.knight_to}, checking the king on {grade.feedback.fork_geometry.king.square} and attacking the {grade.feedback.fork_geometry.major.piece} on {grade.feedback.fork_geometry.major.square}.</p>}
          {grade.feedback?.knight_route.length ? <p>Knight route: {grade.feedback.knight_route.map((hop) => `${hop.from_square}–${hop.to_square}`).join(", ")}.</p> : null}
          {grade.feedback?.sound_moves.length ? <p>Sound defensive ideas: {grade.feedback.sound_moves.join(", ")}.</p> : null}
          {grade.feedback?.refutation_uci.length ? <p>Continuation: {continuationNotation(card.startingFen, grade.feedback.refutation_uci)}</p> : null}
          {grade.feedback?.source_game_id && <p>Source game: {grade.feedback.source_game_url
            ? <a href={grade.feedback.source_game_url} target="_blank" rel="noreferrer">{grade.feedback.source_game_id}</a>
            : grade.feedback.source_game_id}</p>}
          <button onClick={() => void onAdvance().catch(() => setSaveError("Result saved, but the next card could not load. Retry Continue."))}>Continue</button>
        </>}
      </aside>
    </section>
  );

  async function runBury() {
    if (burying) return;
    setBurying(true); setBuryError("");
    try { await onBury(); }
    catch (reason) { setBuryError(`Could not bury this card. ${reason instanceof Error ? reason.message : "Retry the action."}`); }
    finally { setBurying(false); }
  }
}
