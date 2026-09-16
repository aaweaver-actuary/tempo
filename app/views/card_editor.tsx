import { Square, Chess } from "chess.js";
import { useEffect, useMemo, useState } from "react";
import { MoveNavigator } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { pieceSymbols } from "../const";
import {
  PracticeCard,
  PackagedPuzzle,
  asCardId,
  asFenString,
  asSanMove,
} from "../types";
import { API_URL, assetUrl } from "../const";
import { usesLocalApi } from "../utils/local";
import { convertPackagedPuzzleRecordIntoPracticeCard } from "../utils/cards";
import { editFenSquare, fenAfterMoves, moveFenPiece } from "../utils/fen";
import CloseButton from "../components/buttons/CloseButton";

// CardEditor component allows editing a practice card's starting position and solution moves.
//
// State:
// - fen: the current board position in FEN notation
// - solution: the list of moves in SAN notation
// - cursor: the current position in the solution move list
// - tab: whether the user is editing the position or the solution
// - historyMode: whether to preserve or reset the move history when editing the position
// - piece: the currently selected piece for editing the position
// - error: any error message related to illegal moves or invalid FEN
export default function CardEditor({
  practiceCard: card,
  boardTheme: theme,
  pieceSet,
  onClose,
  onSave,
}: {
  practiceCard: PracticeCard;
  boardTheme: BoardTheme;
  pieceSet: PieceSet;
  onClose: () => void;
  onSave: (card: PracticeCard) => void;
}) {
  const [currentFenString, setCurrentFenString] = useState(card.startingFen);
  const [solutionSanMovesList, setSolutionSanMovesList] = useState(card.moves);
  const [currentPositionInMoveList, setCurrentPositionInMoveList] = useState(0); // starts at index 0
  const [tab, setTab] = useState<"position" | "solution">("position");
  const [historyMode, setHistoryMode] = useState<"preserve" | "reset">(
    "preserve",
  );
  const [piece, setPiece] = useState("B");
  const [error, setError] = useState("");

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
      const records = (await response.json()) as PackagedPuzzle[];
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
        const result = (await response.json()) as {
          card_id: string;
          detail?: string;
        };
        if (!response.ok)
          throw new Error(result.detail ?? "Could not save this card.");
        backendId = asCardId(result.card_id);
      }
      onSave({
        ...card,
        backendId,
        revision: (card.revision ?? 1) + 1,
        startingFen: currentFenString,
        moves: solutionSanMovesList,
      });
      onClose();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "The position or solution contains an illegal move.",
      );
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="card-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="card-editor-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <CloseButton onClose={onClose} />
        <div className="editor-heading">
          <div>
            <p className="eyebrow">Card repair</p>
            <h2 id="card-editor-title">Edit {card.title}</h2>
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
                    asFenString(editFenSquare(current, square, piece)),
                  );
              }}
              onFreeMove={(from, to) =>
                setCurrentFenString((current) =>
                  asFenString(moveFenPiece(current, from, to)),
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
            <label>
              FEN
              <textarea
                value={currentFenString}
                onChange={(event) => {
                  try {
                    setCurrentFenString(asFenString(event.target.value));
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
            <fieldset>
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
            </fieldset>
            {error && <p className="editor-error">{error}</p>}
            <div className="editor-actions">
              <button onClick={onClose}>Cancel</button>
              <button className="primary-button" onClick={save}>
                Validate & save
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
