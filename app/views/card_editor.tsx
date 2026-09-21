import { useRef as useDialogRef } from "react";
import { useDialogFocus } from "../hooks/use-dialog-focus";
import { reportDebugError } from "../lib/debug-reporting";
import { Square, Chess } from "chess.js";
import { useEffect, useMemo, useState } from "react";
import { MoveNavigator } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { pieceSymbols } from "../const";
import {
  BuilderSession,
  PracticeCard,
  asFenString,
  asSanMove,
  asUciMove,
} from "../types";
import { API_URL, assetUrl } from "../const";
import { usesLocalApi } from "../utils/local";
import { convertPackagedPuzzleRecordIntoPracticeCard } from "../utils/cards";
import { editFenSquare, fenAfterMoves, moveFenPiece } from "../utils/fen";
import CloseButton from "../components/buttons/CloseButton";
import {
  cardRevisionResultSchema,
  packagedPuzzleSchema,
  prefixSplitResponseSchema,
} from "../domain/schemas";
import { readJsonResponse, validRecords } from "../lib/validated-data";
import { movesToSanFormat } from "../utils/chess";
import type { z } from "zod";

type PrefixSplitPreview = z.infer<typeof prefixSplitResponseSchema>;

export default function CardEditor({
  practiceCard: card,
  boardTheme: theme,
  pieceSet,
  onClose,
  onSave,
  onOpenBuilderForLineRemoval,
}: {
  practiceCard: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  onClose: () => void;
  onSave: (card: PracticeCard) => void;
  onOpenBuilderForLineRemoval?: (session: BuilderSession) => void;
}) {
  const dialogRef = useDialogRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [currentFenString, setCurrentFenString] = useState<string>(
    card.startingFen,
  );
  const [solutionSanMovesList, setSolutionSanMovesList] = useState(card.moves);
  const [currentPositionInMoveList, setCurrentPositionInMoveList] = useState(0); // starts at index 0
  const [tab, setTab] = useState<"position" | "solution">("position");
  const [historyMode, setHistoryMode] = useState<"preserve" | "reset">(
    "preserve",
  );
  const [piece, setPiece] = useState("B");
  const [error, setError] = useState("");
  const [prefixSplitPreview, setPrefixSplitPreview] =
    useState<PrefixSplitPreview>();

  useEffect(() => {
    if (
      card.editingIntent !== "shorten-prefix" ||
      !card.backendId ||
      !usesLocalApi()
    )
      return;
    let active = true;
    void fetch(`${API_URL}/api/cards/${card.backendId}/prefix-split`)
      .then((response) =>
        readJsonResponse(response, prefixSplitResponseSchema, "prefix split preview"),
      )
      .then((preview) => {
        if (!active) return;
        setPrefixSplitPreview(preview);
        setCurrentFenString(preview.parent.starting_fen);
        setSolutionSanMovesList(
          movesToSanFormat(preview.parent.starting_fen, preview.parent.moves).map(
            asSanMove,
          ),
        );
      })
      .catch((failure) => {
        reportDebugError(failure, {
          kind: "api",
          source: "card-editor",
          operation: "preview shorter prefix",
          endpoint: `${API_URL}/api/cards/${card.backendId}/prefix-split`,
        });
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "Could not preview the shorter prefix.",
          );
      });
    return () => {
      active = false;
    };
  }, [card.backendId, card.editingIntent]);

  const previewFen = useMemo(() => {
    try {
      return fenAfterMoves(
        solutionSanMovesList,
        currentPositionInMoveList,
        currentFenString,
      );
    } catch {
      return currentFenString;
    }
  }, [currentPositionInMoveList, currentFenString, solutionSanMovesList]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (
        event.target instanceof HTMLTextAreaElement ||
        event.target instanceof HTMLInputElement
      )
        return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setCurrentPositionInMoveList((value) => Math.max(0, value - 1));
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setCurrentPositionInMoveList((value) =>
          Math.min(solutionSanMovesList.length, value + 1),
        );
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setCurrentPositionInMoveList(0);
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setCurrentPositionInMoveList(solutionSanMovesList.length);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, solutionSanMovesList.length]);

  function playSolution(from: Square, to: Square) {
    const board = new Chess(previewFen);
    try {
      const move = board.move({ from, to, promotion: "q" });
      setSolutionSanMovesList((moves) => [
        ...moves.slice(0, currentPositionInMoveList),
        asSanMove(move.san),
      ]);
      setCurrentPositionInMoveList((value) => value + 1);
      setError("");
    } catch {
      setError("That move is not legal from this position.");
    }
  }

  async function restoreOriginal() {
    try {
      const response = await fetch(assetUrl("data/tactics-decks.json"));
      if (!response.ok) throw new Error();
      const raw: unknown = await response.json();
      if (!Array.isArray(raw)) throw new Error();
      const records = validRecords(
        packagedPuzzleSchema,
        raw,
        "original tactic records",
      );
      const id = card.sourceUrl?.split("/").at(-1);
      const record = records.find((puzzle) => puzzle.PuzzleId === id);
      const original =
        record && convertPackagedPuzzleRecordIntoPracticeCard(record);
      if (!original) throw new Error();
      setCurrentFenString(original.startingFen);
      setSolutionSanMovesList(original.moves);
      setCurrentPositionInMoveList(0);
      setError("");
    } catch {
      setError("The original puzzle record could not be loaded.");
    }
  }

  async function save() {
    try {
      if (card.editingIntent === "shorten-prefix" && usesLocalApi()) {
        if (!card.backendId || !prefixSplitPreview)
          throw new Error("The shorter prefix preview is not ready.");
        const response = await fetch(
          `${API_URL}/api/cards/${card.backendId}/prefix-split`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ expected_revision: card.revision ?? 1 }),
          },
        );
        const result = await readJsonResponse(
          response,
          prefixSplitResponseSchema,
          "accepted prefix split",
        );
        onSave({
          ...card,
          backendId: result.parent.card_id,
          revision: result.source_revision + 1,
          startingFen: asFenString(result.parent.starting_fen),
          moves: movesToSanFormat(
            result.parent.starting_fen,
            result.parent.moves,
          ).map(asSanMove),
          editingIntent: undefined,
        });
        onClose();
        return;
      }
      const board = new Chess(currentFenString);
      const moves = solutionSanMovesList.map((san) => {
        const move = board.move(san);
        return `${move.from}${move.to}${move.promotion ?? ""}`;
      });
      if (!moves.length) throw new Error("Enter at least one solution move.");
      let backendId = card.backendId;
      if (usesLocalApi()) {
        if (!backendId)
          throw new Error("This card is not in the local database.");
        const response = await fetch(`${API_URL}/api/cards/${backendId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            starting_fen: currentFenString,
            moves,
            history_mode: historyMode,
          }),
        });
        const result = await readJsonResponse(
          response,
          cardRevisionResultSchema,
          "card revision",
        );
        backendId = result.card_id;
      }
      onSave({
        ...card,
        backendId,
        revision: (card.revision ?? 1) + 1,
        startingFen: asFenString(currentFenString),
        moves: solutionSanMovesList,
      });
      onClose();
    } catch (error) {
      reportDebugError(error, {
        kind: "api",
        source: "card-editor",
        operation: "save card revision",
        endpoint: `${API_URL}/api/cards/${card.backendId}`,
        method: "PATCH",
      });
      setError(
        error instanceof Error
          ? error.message
          : "The position or solution contains an illegal move.",
      );
    }
  }

  function openBuilderForLineRemoval() {
    try {
      const position = new Chess(currentFenString);
      const history = solutionSanMovesList.map((san) => {
        const move = position.move(san);
        return {
          san: asSanMove(move.san),
          uci: asUciMove(`${move.from}${move.to}${move.promotion ?? ""}`),
          fen: asFenString(position.fen()),
        };
      });
      const orientation =
        card.orientation ?? (position.turn() === "b" ? "black" : "white");
      onOpenBuilderForLineRemoval?.({
        version: 1,
        activeRepertoireByColor: {},
        activeRepertoireId: card.repertoireId,
        orientation,
        startingFen: asFenString(currentFenString),
        history,
        cursor: Math.min(currentPositionInMoveList, history.length),
        branchStart: null,
        dismissedTranspositions: [],
      });
      onClose();
    } catch {
      setError(
        "The line cannot be opened in Builder until all moves are legal.",
      );
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="card-editor"
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="card-editor-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <CloseButton onClose={onClose} />
        <div className="editor-heading">
          <div>
            <p className="eyebrow">Card repair</p>
            <h2 id="card-editor-title">
              {card.editingIntent === "shorten-prefix"
                ? `Shorten ${card.title}`
                : `Edit ${card.title}`}
            </h2>
          </div>
          {card.sourceUrl && (
            <button onClick={restoreOriginal}>Restore Lichess original</button>
          )}
        </div>
        <div className="editor-tabs">
          <button
            className={tab === "position" ? "active" : ""}
            onClick={() => setTab("position")}
          >
            Position
          </button>
          <button
            className={tab === "solution" ? "active" : ""}
            onClick={() => setTab("solution")}
          >
            Solution
          </button>
        </div>
        <div className="editor-layout">
          <div className="editor-board-column">
            {tab === "position" && (
              <div className="piece-palette">
                {Object.entries(pieceSymbols).map(([id, symbol]) => (
                  <button
                    className={piece === id ? "active" : ""}
                    key={id || "remove"}
                    onClick={() => setPiece(id)}
                    aria-label={id ? `Place ${id}` : "Remove piece"}
                  >
                    {symbol}
                  </button>
                ))}
              </div>
            )}
            <Chessboard
              fen={tab === "position" ? currentFenString : previewFen}
              locked={false}
              showHint={false}
              theme={theme}
              pieceSet={pieceSet}
              editMode={tab === "position"}
              onSquareSelect={(square) => {
                if (tab === "position")
                  setCurrentFenString((current) =>
                    editFenSquare(current, square, piece),
                  );
              }}
              onFreeMove={(from, to) =>
                setCurrentFenString((current) =>
                  moveFenPiece(current, from, to),
                )
              }
              onMove={playSolution}
            />
            {tab === "solution" && (
              <>
                <MoveNavigator
                  cursor={currentPositionInMoveList}
                  length={solutionSanMovesList.length}
                  onChange={setCurrentPositionInMoveList}
                />
                <div className="solution-line">
                  {solutionSanMovesList.length ? (
                    solutionSanMovesList.map((move, index) => (
                      <button
                        className={
                          index < currentPositionInMoveList ? "shown" : ""
                        }
                        key={`${move}-${index}`}
                        onClick={() => setCurrentPositionInMoveList(index + 1)}
                      >
                        {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}
                        {move}
                      </button>
                    ))
                  ) : (
                    <span>Play the solution on the board.</span>
                  )}
                </div>
              </>
            )}
          </div>
          <div className="editor-fields">
            {card.editingIntent === "shorten-prefix" && prefixSplitPreview && (
              <section className="shorten-suggestion" aria-label="Prefix split preview">
                <strong>One shorter prefix plus one continuation card</strong>
                <p>
                  The continuation starts from the shortened position and tests
                  exactly one of your moves.
                </p>
                <small>
                  Continuation: {movesToSanFormat(
                    prefixSplitPreview.continuation.starting_fen,
                    prefixSplitPreview.continuation.moves,
                  ).join(" ")}
                </small>
              </section>
            )}
            <label>
              FEN
              <textarea
                value={currentFenString}
                onChange={(event) => {
                  try {
                    setCurrentFenString(event.target.value);
                    setError("");
                  } catch {
                    setError("Enter a valid FEN before saving.");
                  }
                  setCurrentPositionInMoveList(0);
                }}
              />
            </label>
            <p className="editor-key-help">
              ←/→ step · ↑ start · ↓ end · Esc close
            </p>
            {card.editingIntent !== "shorten-prefix" && <fieldset>
              <legend>Scheduling history</legend>
              <label>
                <input
                  type="radio"
                  checked={historyMode === "preserve"}
                  onChange={() => setHistoryMode("preserve")}
                />{" "}
                Preserve history
              </label>
              <label>
                <input
                  type="radio"
                  checked={historyMode === "reset"}
                  onChange={() => setHistoryMode("reset")}
                />{" "}
                Reset as a new card
              </label>
            </fieldset>}
            {error && <p className="editor-error">{error}</p>}
            <div className="editor-actions">
              {usesLocalApi() && card.kind === "opening" && (
                <button onClick={openBuilderForLineRemoval}>
                  Open Builder to remove line
                </button>
              )}
              <button onClick={onClose}>Cancel</button>
              <button className="primary-button" onClick={save}>
                {card.editingIntent === "shorten-prefix"
                  ? "Accept split"
                  : "Validate & save"}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
