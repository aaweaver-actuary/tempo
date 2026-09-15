"use client";
import { useState } from "react";
import {
  type BoardTheme,
  type PieceSet,
  Chessboard,
} from "../components/chessboard";
import { demoCards } from "../samples";
import { fenAfterMoves } from "../utils/fen";
import CloseButton from "../components/CloseButton";
import { lichessAnalysisUrl } from "../utils/urls";
import AnalyzeThisButton from "../components/AnalyzeThisButton";

export function TreeBrowser({
  onClose,
  theme,
  pieceSet,
}: {
  onClose: () => void;
  theme: BoardTheme;
  pieceSet: PieceSet;
}) {
  const line = demoCards[0].moves;
  const [ply, setPly] = useState(0);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="tree-browser"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tree-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <CloseButton onClose={onClose} />
        <div className="tree-heading">
          <div>
            <p className="eyebrow">Repertoire browser</p>
            <h2 id="tree-title">1. e4 Main Lines</h2>
          </div>
          <span>
            {ply === 0 ? "Starting position" : `${ply} plies from start`}
          </span>
        </div>
        <div className="tree-layout">
          <div className="tree-board">
            <Chessboard
              fen={fenAfterMoves(line, ply)}
              locked
              showHint={false}
              theme={theme}
              pieceSet={pieceSet}
              onMove={() => undefined}
            />
          </div>
          <div className="tree-panel">
            <div className="tree-path">
              <button
                className={ply === 0 ? "current" : ""}
                onClick={() => setPly(0)}
              >
                Start
              </button>
              {line.map((move, index) => (
                <button
                  className={ply === index + 1 ? "current" : ""}
                  key={`${move}-${index}`}
                  onClick={() => setPly(index + 1)}
                >
                  <span>
                    {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : "…"}
                  </span>
                  {move}
                </button>
              ))}
            </div>
            <div className="branch-list">
              <span>Branches from the first move</span>
              <button className="selected">
                <b>1… c5</b>
                <small>Open Sicilian · 168 cards</small>
              </button>
              <button>
                <b>1… e6</b>
                <small>French Defense · 74 cards</small>
              </button>
              <button>
                <b>1… c6</b>
                <small>Caro-Kann · 51 cards</small>
              </button>
            </div>
            <AnalyzeThisButton line={line} ply={ply} />
          </div>
        </div>
      </section>
    </div>
  );
}
