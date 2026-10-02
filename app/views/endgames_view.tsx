import { useBoardHistory } from "../hooks/use-board-history";
import { TextInput } from "../components/inputs/TextInput";
import { Button } from "../components/buttons/BaseButton";
import { Dialog } from "../components/dialog";
import { BoardTools } from "../components/board/board-workspace";
import { API_URL, STANDARD_FEN } from "../const";
import {
  readWorkspaceResponse,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { Square, Chess } from "chess.js";
import { useState, useEffect, useCallback, useRef } from "react";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { generateLegalEndgameFen } from "../lib/endgame-generator";
import { endgameTemplates } from "../samples";
import { probeTablebase, tablebaseCategoryForWhite } from "../utils/tablebase";
import { usesLocalApi } from "../utils/local";
import { PracticeCard, PieceColor } from "../types";
import { EndgameMaterial } from "../lib/endgame-generator";
import { OutcomeFlash } from "../components/board/OutcomeFlash";
import {
  endgameTemplatesSchema,
} from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { useTaskTabs } from "../components/task-tabs";
import { reportDebugError } from "../lib/debug-reporting";
import { admitEndgameTemplate } from "../lib/endgame-template-command";
import { startEndgameAttempt } from "../lib/endgame-attempt-command";

const TEMPLATE_API_ENDPOINT = `${API_URL}/api/endgames/templates`;

export default function EndgamesView({
  theme,
  pieceSet,
  onQueueChanged,
  scheduledCard,
  onReview,
  onBury,
  blocked = false,
  useSharedBoard = false,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  onQueueChanged: () => void;
  scheduledCard?: PracticeCard;
  onReview?: (outcome: "correct" | "again") => void;
  onBury?: () => Promise<void>;
  blocked?: boolean;
  useSharedBoard?: boolean;
}) {
  const [templates, setTemplates] =
    useState<(EndgameMaterial & { side?: PieceColor })[]>(endgameTemplates);
  const [editingMaterial, setEditingMaterial] = useState(false);
  const [materialDraft, setMaterialDraft] = useState({
    white: "KR",
    black: "K",
    side: "white" as PieceColor,
  });
  const [outcome, setOutcome] = useState<"correct" | "wrong" | null>(null);
  const generation = useRef(0);
  const [positionGeneration, setPositionGeneration] = useState(0);
  const resultTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [selected, setSelected] = useState(1);
  const [classification, setClassification] = useState<"win" | "draw" | null>(
    null,
  );
  const [target, setTarget] = useState<"win" | "draw">("win");
  const [status, setStatus] = useState("Finding a legal tablebase position…");
  const [fen, setFen] = useState(STANDARD_FEN);
  const [playedPositions, setPlayedPositions] = useState<string[]>([STANDARD_FEN]);
  const [busy, setBusy] = useState(true);
  const [userMoves, setUserMoves] = useState(0);
  const [complete, setComplete] = useState(false);
  const [burying, setBurying] = useState(false);
  const [buryError, setBuryError] = useState("");
  const [admitted, setAdmitted] = useState<
    Record<number, { templateId: string; cardId: string }>
  >({});
  const tools = useTaskTabs(
    ["Study", "Positions"],
    "Study",
    "tempo-endgames-tools",
  );

  useEffect(() => {
    if (!usesLocalApi()) return;
    readWorkspaceResponse(TEMPLATE_API_ENDPOINT)
      .then((response) =>
        readJsonResponse(response, endgameTemplatesSchema, "endgame templates"),
      )
      .then((data) => {
        const next: Record<number, { templateId: string; cardId: string }> = {};
        const allTemplates = [...endgameTemplates];
        for (const item of data.templates) {
          if (
            !allTemplates.some(
              (template) =>
                template.white === item.white_material &&
                template.black === item.black_material,
            )
          )
            allTemplates.push({
              name: `${item.white_material} vs ${item.black_material}`,
              white: item.white_material,
              black: item.black_material,
            });
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
          const index = Object.entries(next).find(
            ([, item]) => item.cardId === scheduledCard.backendId,
          )?.[0];
          if (index !== undefined) setSelected(Number(index));
        }
      })
      .catch(() => undefined);
  }, [scheduledCard]);

  async function admitTemplate() {
    if (!usesLocalApi()) return;
    const template = templates[selected];
    try {
      const data = await admitEndgameTemplate({
        name: template.name,
        white_material: template.white,
        black_material: template.black,
        trained_color: template.side ?? "white",
        goal_mix: "both",
      });
      invalidateWorkspaceData();
      setAdmitted((current) => ({
        ...current,
        [selected]: { templateId: data.id, cardId: data.card_id },
      }));
      setStatus("Added to your daily training.");
      onQueueChanged();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not add this material set to training.");
      return;
    }
  }

  const recordEndgame = useCallback(
    (result: "correct" | "again") => {
      setOutcome(result === "correct" ? "correct" : "wrong");
      if (!scheduledCard || !onReview) return;
      const token = generation.current;
      clearTimeout(resultTimer.current);
      resultTimer.current = setTimeout(() => {
        if (token === generation.current) onReview(result);
      }, 750);
    },
    [scheduledCard, onReview],
  );

  const newPosition = useCallback(
    async (index = selected) => {
      setPositionGeneration(generation.current + 1);
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
          const attempt = await startEndgameAttempt(item.templateId);
          if (token !== generation.current) return;
          setFen(attempt.fen);
          setPlayedPositions([attempt.fen]);
          setTarget(attempt.target);
          setBusy(false);
          setStatus("Win or draw?");
        } catch (error) {
          reportDebugError(error, {
            kind: "api",
            source: "endgames-view",
            operation: "generate endgame attempt",
            endpoint: `${TEMPLATE_API_ENDPOINT}/${item.templateId}/attempt`,
            method: "POST",
          });
          if (token === generation.current) {
            setBusy(false);
            setStatus(
              error instanceof Error
                ? error.message
                : "Could not generate the position.",
            );
          }
        }
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
          setPlayedPositions([candidate]);
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
  useEffect(
    () => () => {
      generation.current += 1;
      clearTimeout(resultTimer.current);
    },
    [],
  );

  function classify(value: "win" | "draw") {
    if (busy || complete) return;
    setClassification(value);
    setStatus(
      value === target
        ? `Correct · now ${target === "win" ? "convert the win" : "hold the draw"}.`
        : `This position is a ${target}. Try the classification again.`,
    );
    if (value !== target) {
      setComplete(true);
      recordEndgame("again");
    }
  }

  const play = useCallback(
    async (from: Square, to: Square) => {
      if (blocked || classification !== target || busy || complete) return;
      const board = new Chess(fen);
      const token = generation.current;
      try {
        board.move({ from, to, promotion: "q" });
      } catch {
        return;
      }
      const playedFen = board.fen();
      setFen(playedFen);
      setPlayedPositions(positions => [...positions, playedFen]);
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
        if (scheduledCard?.orientation === "black")
          userCategory =
            userCategory === "win"
              ? "loss"
              : userCategory === "loss"
                ? "win"
                : "draw";
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
          const replyFen = board.fen();
          setFen(replyFen);
          setPlayedPositions(positions => [...positions, replyFen]);
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
        } else if (
          target === "draw" &&
          count >=
            Number(localStorage.getItem("tempo-draw-hold-user-moves") ?? 20)
        ) {
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
    },
    [
      blocked,
      classification,
      target,
      busy,
      complete,
      fen,
      scheduledCard,
      userMoves,
      recordEndgame,
    ],
  );

  const endgamePositionKey = `${scheduledCard?.queueEntryId ?? scheduledCard?.id ?? selected}:${scheduledCard?.queueCycle ?? 0}:${scheduledCard?.revision ?? 1}`;
  const boardHistory = useBoardHistory(`${endgamePositionKey}:${positionGeneration}`, playedPositions, fen);
  useBoardPublisher("endgames", useSharedBoard ? {
    keyboard: boardHistory.keyboard,
    positionKey: endgamePositionKey,
    unavailable: fen === STANDARD_FEN ? status : undefined,
    fen: boardHistory.fen,
    interactionMode:
      boardHistory.viewingHistory || blocked || busy || classification !== target || complete ? "readonly" : "legal",
    showHint: false,
    theme,
    pieceSet,
    orientation: scheduledCard?.orientation === "black" ? "black" : "white",
    positionRevision: userMoves,
    onMove: (from, to) => void play(from, to),
    onSquareSelect: undefined,
    onFreeMove: undefined,
    onDrawnShapesChange: undefined,
    onFlip: undefined,
  } : null);

  return (
    <section
      inert={blocked}
      className={`endgames-page${scheduledCard ? " scheduled-endgame" : ""}`}
      {...tools.panelProps}
    >
      <div className="workspace-title">
        <div>
          <h1>Endgames</h1>
        </div>
        {!scheduledCard && (
          <Button
            variant="primary"
            className="primary-button"
            disabled={Boolean(admitted[selected]) || !usesLocalApi()}
            onClick={() => void admitTemplate()}
          >
            {admitted[selected]
              ? "✓ In daily training"
              : "＋ Add to daily training"}
          </Button>
        )}
      </div>
      {!scheduledCard && (
        <div className="workspace-context-tabs">{tools.tabs}</div>
      )}
      <div className="endgame-workspace">
        {!scheduledCard && tools.activeTab === "Positions" && (
          <aside className="template-list">
            {templates.map((template, index) => (
              <Button
                className={selected === index ? "active" : ""}
                key={template.name}
                onClick={() => setSelected(index)}
              >
                <strong>{template.name}</strong>
                <small>
                  {template.white} vs {template.black} · White
                </small>
              </Button>
            ))}
          </aside>
        )}
        <div className="board-column centered-board">
          {!useSharedBoard && (
            <Chessboard
              positionKey={endgamePositionKey}
              keyboard={boardHistory.keyboard}
              fen={boardHistory.fen}
              locked={boardHistory.viewingHistory || blocked || busy || classification !== target || complete}
              showHint={false}
              theme={theme}
              pieceSet={pieceSet}
              onMove={(from, to) => void play(from, to)}
              orientation={scheduledCard?.orientation}
            />
          )}
          <BoardTools>
            {scheduledCard && onBury && (
              <Button
                type="button"
                disabled={blocked || busy || burying || complete}
                onClick={() => void runBury()}
              >
                {burying ? "Burying…" : "Bury"}
              </Button>
            )}
            <Button
              disabled={Boolean(scheduledCard) && !complete}
              onClick={() => void newPosition()}
            >
              ⤨ <span>New position</span>
            </Button>
            {!scheduledCard && (
              <Button
                onClick={(event) => {
                  event.currentTarget.focus();
                  setEditingMaterial(true);
                }}
              >
                ⚙ <span>Edit material</span>
              </Button>
            )}
          </BoardTools>
          {outcome && <OutcomeFlash outcome={outcome} />}
          {buryError && (
            <p role="alert">
              {buryError}
            </p>
          )}
        </div>
        <aside className="study-panel endgame-study">
          <span className="pill">Material template</span>
          <h2>{templates[selected].name}</h2>
          <p>
            {scheduledCard?.orientation === "black" ? "Black" : "White"} to play
          </p>
          <div className="classification">
            <Button
              className={classification === "win" ? "active" : ""}
              disabled={busy || complete}
              onClick={() => classify("win")}
            >
              Win
            </Button>
            <Button
              className={classification === "draw" ? "active" : ""}
              disabled={busy || complete}
              onClick={() => classify("draw")}
            >
              Draw
            </Button>
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
      {editingMaterial && (
        <Dialog
          className="import-dialog"
          titleId="endgame-material-title"
          onClose={() => setEditingMaterial(false)}
        >
          <h2 id="endgame-material-title">Endgame material</h2>
          <p>Use K, Q, R, B, N, P. One king per side; seven pieces total.</p>
          {(["white", "black"] as const).map((side) => (
            <label key={side}>
              {side}
              <TextInput
                value={materialDraft[side]}
                onChange={(event) =>
                  setMaterialDraft((current) => ({
                    ...current,
                    [side]: event.target.value.toUpperCase(),
                  }))
                }
              />
            </label>
          ))}
          <Button
            onClick={() => {
              if (
                !/^K[QRBNP]*$/.test(materialDraft.white) ||
                !/^K[QRBNP]*$/.test(materialDraft.black) ||
                materialDraft.white.length + materialDraft.black.length > 7
              ) {
                setStatus(
                  "Use one king per side and at most seven total pieces.",
                );
                return;
              }
              setTemplates((current) => [
                ...current,
                {
                  name: `${materialDraft.white} vs ${materialDraft.black}`,
                  ...materialDraft,
                },
              ]);
              setSelected(templates.length);
              setEditingMaterial(false);
            }}
          >
            Practice material
          </Button>
          <Button onClick={() => setEditingMaterial(false)}>Cancel</Button>
        </Dialog>
      )}
    </section>
  );

  async function runBury() {
    if (!onBury || burying) return;
    setBurying(true);
    setBuryError("");
    try {
      await onBury();
    } catch (error) {
      setBuryError(
        `Could not bury this card. ${error instanceof Error ? error.message : "Retry the action."}`,
      );
    } finally {
      setBurying(false);
    }
  }
}
