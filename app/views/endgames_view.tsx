import { API_URL, STANDARD_FEN } from "../const";
import { readWorkspaceResponse, invalidateWorkspaceData } from "../lib/workspace-data";
import { Square, Chess } from "chess.js";
import { useState, useEffect, useCallback, useRef } from "react";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { generateLegalEndgameFen } from "../lib/endgame-generator";
import { endgameTemplates } from "../samples";
import { probeTablebase, tablebaseCategoryForWhite } from "../utils/tablebase";
import { usesLocalApi } from "../utils/local";
import { PracticeCard, PieceColor } from "../types";
import { EndgameMaterial } from "../lib/endgame-generator";
import { OutcomeFlash } from "../components/board-controls";

const TEMPLATE_API_ENDPOINT = `${API_URL}/api/endgames/templates`;

export default function EndgamesView({
  theme,
  pieceSet,
  onQueueChanged,
  scheduledCard,
  onReview,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  onQueueChanged: () => void;
  scheduledCard?: PracticeCard;
  onReview?: (outcome: "correct" | "again") => void;
}) {
  const [templates, setTemplates] = useState<(EndgameMaterial & { side?: PieceColor })[]>(endgameTemplates);
  const [editingMaterial, setEditingMaterial] = useState(false);
  const [materialDraft, setMaterialDraft] = useState({ white: "KR", black: "K", side: "white" as PieceColor });
  const [outcome, setOutcome] = useState<"correct" | "wrong" | null>(null);
  const generation = useRef(0);
  const resultTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [selected, setSelected] = useState(1);
  const [classification, setClassification] = useState<"win" | "draw" | null>(
    null,
  );
  const [target, setTarget] = useState<"win" | "draw">("win");
  const [status, setStatus] = useState("Finding a legal tablebase position…");
  const [fen, setFen] = useState(STANDARD_FEN);
  const [busy, setBusy] = useState(true);
  const [userMoves, setUserMoves] = useState(0);
  const [complete, setComplete] = useState(false);
  const [admitted, setAdmitted] = useState<
    Record<number, { templateId: string; cardId: string }>
  >({});

  useEffect(() => {
    if (!usesLocalApi()) return;
    readWorkspaceResponse(TEMPLATE_API_ENDPOINT)
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((value) => {
        const data = value as {
          templates: Array<{
            id: string;
            card_id: string;
            white_material: string;
            black_material: string;
          }>;
        };
        const next: Record<number, { templateId: string; cardId: string }> = {};
        const allTemplates = [...endgameTemplates];
        for (const item of data.templates) {
          if (!allTemplates.some((template) => template.white === item.white_material && template.black === item.black_material)) allTemplates.push({ name: `${item.white_material} vs ${item.black_material}`, white: item.white_material, black: item.black_material });
        }
        setTemplates(allTemplates);
        allTemplates.forEach((template, index) => {
          const found = data.templates.find(
            (item) =>
              item.white_material === template.white &&
              item.black_material === template.black,
          );
          if (found)
            next[index] = { templateId: found.id, cardId: found.card_id };
        });
        setAdmitted(next);
        if (scheduledCard) {
          const index = Object.entries(next).find(([, item]) => item.cardId === scheduledCard.backendId)?.[0];
          if (index !== undefined) setSelected(Number(index));
        }
      })
      .catch(() => undefined);
  }, [scheduledCard]);

  async function admitTemplate() {
    if (!usesLocalApi()) return;
    const template = templates[selected];
    const response = await fetch(`${API_URL}/api/endgames/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: template.name,
        white_material: template.white,
        black_material: template.black,
        trained_color: template.side ?? "white",
        goal_mix: "both",
      }),
    });
    if (!response.ok) {
      setStatus("Could not add this material set to training.");
      return;
    }
    const data = (await response.json()) as { id: string; card_id: string };
    invalidateWorkspaceData();
    setAdmitted((current) => ({
      ...current,
      [selected]: { templateId: data.id, cardId: data.card_id },
    }));
    setStatus("Added to your daily training.");
    onQueueChanged();
  }

  function recordEndgame(result: "correct" | "again") {
    setOutcome(result === "correct" ? "correct" : "wrong");
    if (!scheduledCard || !onReview) return;
    const token = generation.current;
    clearTimeout(resultTimer.current);
    resultTimer.current = setTimeout(() => { if (token === generation.current) onReview(result); }, 750);
  }

  const newPosition = useCallback(
    async (index = selected) => {
      const token = ++generation.current;
      clearTimeout(resultTimer.current);
      setBusy(true);
      setOutcome(null);
      setClassification(null);
      setComplete(false);
      setUserMoves(0);
      setStatus("Finding a legal tablebase position…");
      if (scheduledCard) {
        const item = admitted[index];
        if (!item) return;
        try {
          const response = await fetch(`${TEMPLATE_API_ENDPOINT}/${item.templateId}/attempt`, { method: "POST" });
          const attempt = await response.json() as { fen: string; target: "win" | "draw"; detail?: string };
          if (!response.ok) throw new Error(attempt.detail);
          if (token !== generation.current) return;
          setFen(attempt.fen); setTarget(attempt.target); setBusy(false); setStatus("Win or draw?");
        } catch (error) { if (token === generation.current) { setBusy(false); setStatus(error instanceof Error ? error.message : "Could not generate the position."); } }
        return;
      }
      for (let attempt = 0; attempt < 24; attempt += 1) {
        const candidate = generateLegalEndgameFen(templates[index]);
        try {
          const result = await probeTablebase(candidate);
          const category = tablebaseCategoryForWhite(
            candidate,
            result.category,
          );
          if (category === "loss") continue;
          if (token !== generation.current) return;
          setFen(candidate);
          setTarget(category);
          setStatus("Classify the position before playing.");
          setBusy(false);
          return;
        } catch {
          /* Try another legal position before reporting the service unavailable. */
        }
      }
      setStatus(
        "The tablebase could not provide a position. Try again when online.",
      );
      setBusy(false);
    },
    [selected, templates, admitted, scheduledCard],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void newPosition(selected);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [newPosition, selected]);
  useEffect(() => () => { generation.current += 1; clearTimeout(resultTimer.current); }, []);

  function classify(value: "win" | "draw") {
    if (busy || complete) return;
    setClassification(value);
    setStatus(
      value === target
        ? `Correct · now ${target === "win" ? "convert the win" : "hold the draw"}.`
        : `This position is a ${target}. Try the classification again.`,
    );
    if (value !== target) { setComplete(true); recordEndgame("again"); }
  }

  async function play(from: Square, to: Square) {
    if (classification !== target || busy || complete) return;
    const board = new Chess(fen);
    const token = generation.current;
    try {
      board.move({ from, to, promotion: "q" });
    } catch {
      return;
    }
    setFen(board.fen());
    setBusy(true);
    if (board.isCheckmate()) {
      setComplete(true);
      setStatus("Converted · template review complete.");
      setBusy(false);
      recordEndgame("correct");
      return;
    }
    try {
      const afterUser = await probeTablebase(board.fen());
      if (token !== generation.current) return;
      let userCategory = tablebaseCategoryForWhite(
        board.fen(),
        afterUser.category,
      );
      if (scheduledCard?.orientation === "black") userCategory = userCategory === "win" ? "loss" : userCategory === "loss" ? "win" : "draw";
      if (
        (target === "win" && userCategory !== "win") ||
        (target === "draw" && userCategory === "loss")
      ) {
        setComplete(true);
        setStatus(`Failed · the position is now a ${userCategory}.`);
        setBusy(false);
        recordEndgame("again");
        return;
      }
      const defense = afterUser.moves?.[0]?.uci;
      if (defense) {
        board.move({
          from: defense.slice(0, 2) as Square,
          to: defense.slice(2, 4) as Square,
          promotion: defense[4],
        });
        setFen(board.fen());
      }
      const count = userMoves + 1;
      setUserMoves(count);
      if (board.isGameOver()) {
        const success = target === "draw" && !board.isCheckmate();
        setComplete(true);
        setStatus(
          success
            ? "Draw secured · template review complete."
            : "The defender held the position.",
        );
        recordEndgame(success ? "correct" : "again");
      } else if (target === "draw" && count >= Number(localStorage.getItem("tempo-draw-hold-user-moves") ?? 20)) {
        setComplete(true);
        setStatus("Draw held for 20 moves · template review complete.");
        recordEndgame("correct");
      } else
        setStatus(
          `${target === "win" ? "Winning" : "Drawing"} status preserved · tablebase defense played.`,
        );
    } catch {
      setFen(fen);
      setStatus(
        "The tablebase response failed. Replay the move when online.",
      );
    }
    setBusy(false);
  }

  return (
    <section className={`endgames-page${scheduledCard ? " scheduled-endgame" : ""}`}>
      <div className="workspace-title">
        <div>
          <h1>Endgames</h1>
          <span>Exact seven-piece practice</span>
        </div>
        {!scheduledCard && <button
          className="primary-button"
          disabled={Boolean(admitted[selected]) || !usesLocalApi()}
          onClick={() => void admitTemplate()}
        >
          {admitted[selected]
            ? "✓ In daily training"
            : "＋ Add to daily training"}
        </button>}
      </div>
      <div className="endgame-workspace">
        {!scheduledCard && <aside className="template-list">
          {templates.map((template, index) => (
            <button
              className={selected === index ? "active" : ""}
              key={template.name}
              onClick={() => setSelected(index)}
            >
              <strong>{template.name}</strong>
              <small>
                {template.white} vs {template.black} · White
              </small>
            </button>
          ))}
        </aside>}
        <div className="board-column centered-board">
          <Chessboard
            fen={fen}
            locked={busy || classification !== target || complete}
            showHint={false}
            theme={theme}
            pieceSet={pieceSet}
            onMove={(from, to) => void play(from, to)}
            orientation={scheduledCard?.orientation}
          />
          <div className="board-tools">
            <button disabled={Boolean(scheduledCard) && !complete} onClick={() => void newPosition()}>
              ⤨ <span>New position</span>
            </button>
            {!scheduledCard && <button onClick={() => setEditingMaterial(true)}>
              ⚙ <span>Edit material</span>
            </button>}
          </div>
          {outcome && <OutcomeFlash outcome={outcome} />}
        </div>
        <aside className="study-panel endgame-study">
          <span className="pill">Material template</span>
          <h2>{templates[selected].name}</h2>
          <p>{scheduledCard?.orientation === "black" ? "Black" : "White"} to play</p>
          <div className="classification">
            <button
              className={classification === "win" ? "active" : ""}
              disabled={busy || complete}
              onClick={() => classify("win")}
            >
              Win
            </button>
            <button
              className={classification === "draw" ? "active" : ""}
              disabled={busy || complete}
              onClick={() => classify("draw")}
            >
              Draw
            </button>
          </div>
          <div
            className={`feedback ${complete && status.startsWith("Failed") ? "wrong" : "ready"}`}
          >
            <span className="feedback-icon">
              {busy ? "…" : outcome === "wrong" ? "×" : complete ? "✓" : "●"}
            </span>
            <div>
              <strong>
                {classification === target
                  ? "Play the position"
                  : "Win or draw?"}
              </strong>
              <p>{status}</p>
            </div>
          </div>
          <small>
            {target === "draw"
              ? `${userMoves} / 20 accurate user moves`
              : "Checkmate completes the card."}
          </small>
        </aside>
      </div>
      {editingMaterial && <div className="modal-backdrop" onClick={() => setEditingMaterial(false)}><section className="import-dialog" role="dialog" aria-modal="true" aria-label="Edit endgame material" onClick={(event) => event.stopPropagation()}><h2>Endgame material</h2><p>Use K, Q, R, B, N, P. One king per side; seven pieces total.</p>{(["white", "black"] as const).map((side) => <label key={side}>{side}<input value={materialDraft[side]} onChange={(event) => setMaterialDraft((current) => ({ ...current, [side]: event.target.value.toUpperCase() }))} /></label>)}<button onClick={() => {
        if (!/^K[QRBNP]*$/.test(materialDraft.white) || !/^K[QRBNP]*$/.test(materialDraft.black) || materialDraft.white.length + materialDraft.black.length > 7) { setStatus("Use one king per side and at most seven total pieces."); return; }
        setTemplates((current) => [...current, { name: `${materialDraft.white} vs ${materialDraft.black}`, ...materialDraft }]); setSelected(templates.length); setEditingMaterial(false);
      }}>Practice material</button><button onClick={() => setEditingMaterial(false)}>Cancel</button></section></div>}
    </section>
  );
}
