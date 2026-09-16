import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { Square, Chess } from "chess.js";
import {
  useState,
  useMemo,
  useEffect,
  useCallback,
  useRef,
  type Dispatch,
  type SetStateAction,
} from "react";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import CandidateMovesTable from "../CandidateMovesTable";
import { STANDARD_FEN, API_URL } from "../const";
import {
  analyzeWithStockfish,
  analyzeWithMaia,
} from "../lib/analysis-engines";
import { adaptEngineMoves, adaptExplorerMoves, type RawExplorerMove } from "../domain/adapters/analysis-adapters";
import {
  LocalRepertoire,
  ExplorerMove,
  CandidateMove,
  UciMove,
  AnalysisLine,
  CanonicalLine,
  PieceColor,
  EngineStatus,
  BuilderSession,
  PositionAnnotation,
  asFenString,
  asRepertoireId,
  asFenKey,
  asIsoDateString,
  asLineId,
  asSanMove,
  asUciMove,
} from "../types";
import { usesLocalApi } from "../utils/local";
import { connectLichess } from "../utils/lichess";
import { Settings } from "../utils/settings";
import {
  canonicalFenKey,
  canonicalizeLine,
  sanForUci,
} from "../utils/canonical-line";
import {
  annotationToShapes,
  loadPositionAnnotation,
  savePositionAnnotation,
  shapesToAnnotationParts,
} from "../utils/position-annotations";
import {
  chessPositionDistance,
  indexRepertoirePositions,
  searchMaiaTranspositions,
  type TranspositionResult,
} from "../lib/position-similarity";
import CloseButton from "../components/buttons/CloseButton";

type AnalysisMetric = "stockfish" | "lichess" | "masters";

function candidatesThrough<T extends { uci: string }>(
  moves: T[],
  weight: (move: T) => number,
  target: number,
): T[] {
  const scored = moves
    .map((move) => ({ move, score: weight(move) }))
    .sort((left, right) => right.score - left.score);
  const total = scored.reduce((sum, value) => sum + value.score, 0);
  let cumulative = 0;
  return scored
    .filter(({ score }) => {
      if (total <= 0 || cumulative / total >= target) return false;
      cumulative += score;
      return true;
    })
    .map(({ move }) => move);
}

function lineMoveName(line: AnalysisLine): string {
  return `${line.title} · ${line.moves.length} moves`;
}

// Returns true if the window object is undefined (i.e., the code is running on the server), false otherwise.
function isCodeRunningOnServer() {
  return typeof window === "undefined";
}

function getLocalStorageOrDefault(key: string, defaultValue: string) {
  return isCodeRunningOnServer()
    ? defaultValue
    : (localStorage.getItem(key) ?? defaultValue);
}

function readBuilderSession(): BuilderSession | undefined {
  if (typeof window === "undefined") return undefined;
  const current = localStorage.getItem("tempo-builder-session");
  const legacy = localStorage.getItem("tempo-analysis-session");
  const raw = current ?? legacy;
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw) as BuilderSession;
    if (parsed.version !== 1 || !Array.isArray(parsed.history))
      return undefined;
    if (!current) {
      localStorage.setItem("tempo-builder-session", raw);
      localStorage.removeItem("tempo-analysis-session");
    }
    return parsed;
  } catch {
    return undefined;
  }
}

export default function BuilderView({
  imported,
  settings,
  theme,
  pieceSet,
}: {
  imported: LocalRepertoire[];
  settings: Settings;
  theme: BoardTheme;
  pieceSet: PieceSet;
}) {
  const initialSession = useMemo(readBuilderSession, []);
  const [history, setHistory] = useState(initialSession?.history ?? []);
  const [cursor, setCursor] = useState(initialSession?.cursor ?? 0);
  const [startingFen, setStartingFen] = useState(
    initialSession?.startingFen ?? STANDARD_FEN,
  );
  const [explorerOn, setExplorerOn] = useState(
    getLocalStorageOrDefault("tempo-explorer-on", "true") === "true",
  );
  const [isStockfishOn, setIsStockfishOn] = useState(
    getLocalStorageOrDefault("tempo-stockfish-on", "true") === "true",
  );
  const [maiaOn, setMaiaOn] = useState(() => {
    const saved = getLocalStorageOrDefault("tempo-maia-on", "true");
    return saved === "true";
  });
  const [maiaElo] = useState(() =>
    isCodeRunningOnServer()
      ? settings.getMaia().elo
      : (localStorage.getItem("tempo-maia-elo") ?? "1500"),
  );
  const [coverageTarget] = useState(() =>
    Number(getLocalStorageOrDefault("tempo-coverage-target", "90")),
  );
  const [engineWindowCp] = useState(() =>
    Number(getLocalStorageOrDefault("tempo-engine-window-cp", "30")),
  );
  const [lichessToken, setLichessToken] = useState(() =>
    getLocalStorageOrDefault("tempo-lichess-token", ""),
  );
  const [explorerMoves, setExplorerMoves] = useState<ExplorerMove[]>([]);
  const [mastersMoves, setMastersMoves] = useState<ExplorerMove[]>([]);
  const [explorerSpeeds] = useState(() =>
    getLocalStorageOrDefault(
      "tempo-explorer-speeds",
      settings.getExplorer().speeds,
    ),
  );
  const [explorerRatings] = useState(() =>
    getLocalStorageOrDefault(
      "tempo-explorer-ratings",
      settings.getExplorer().ratings,
    ),
  );
  const [branchStart, setBranchStart] = useState<number | null>(
    initialSession?.branchStart ?? null,
  );
  const [branchNote, setBranchNote] = useState("");
  const [explorerState, setExplorerState] = useState<EngineStatus>(
    settings.getLichessStatus(),
  );
  const [stockfishMoves, setStockfishMoves] = useState<CandidateMove[]>([]);
  const [stockfishState, setStockfishState] = useState<EngineStatus>(
    settings.getEngine().state,
  );
  const [maiaMoves, setMaiaMoves] = useState<CandidateMove[]>([]);
  const [maiaState, setMaiaState] = useState<EngineStatus>(
    settings.getMaia().state,
  );
  const [maiaProgress, setMaiaProgress] = useState(0);
  const [backendLines, setBackendLines] = useState<AnalysisLine[]>([]);
  const [orientation, setOrientation] = useState<PieceColor>(() => {
    const stored =
      initialSession?.orientation ??
      getLocalStorageOrDefault("tempo-builder-orientation", "white");
    return stored === "white" || stored === "black" ? stored : "white";
  });
  const [activeRepertoire, setActiveRepertoire] = useState(
    () =>
      initialSession?.activeRepertoireId ??
      initialSession?.activeRepertoireByColor?.[
        initialSession.orientation ?? "white"
      ] ??
      getLocalStorageOrDefault("tempo-active-repertoire-white", ""),
  );
  const [arrowMetric] = useState<AnalysisMetric>(
    () => settings.getSettings().arrow_metric ?? "stockfish",
  );
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [currentSearchIndex, setCurrentSearchIndex] = useState(0);
  const [hoveredMove, setHoveredMove] = useState<string | null>(null);
  const [annotation, setAnnotation] = useState<PositionAnnotation>();
  const [annotationStatus, setAnnotationStatus] = useState("");
  const [transpositions, setTranspositions] = useState<TranspositionResult[]>(
    [],
  );
  const [transpositionState, setTranspositionState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const transpositionController = useRef<AbortController | null>(null);

  const visibleHistory = history.slice(0, cursor);
  const fen = visibleHistory.at(-1)?.fen ?? startingFen;
  const previousUci = visibleHistory.at(-1)?.uci;
  const lastMove: [string, string] | undefined = previousUci
    ? [previousUci.slice(0, 2), previousUci.slice(2, 4)]
    : undefined;
  const playedUci = visibleHistory.map((move) => move.uci);
  const availableLines = useMemo<CanonicalLine[]>(() => {
    const browserLines = imported.flatMap((repertoire) =>
      repertoire.cards.map((card) => ({
        id: asLineId(String(card.id)),
        repertoireId: repertoire.id,
        repertoireName: repertoire.title,
        title: card.title,
        side: repertoire.side,
        moves: card.moves,
        startingFen: asFenString(String(card.startingFen)),
      })),
    );
    const source = usesLocalApi() ? backendLines : browserLines;
    const canonical = source.map(canonicalizeLine);
    return [
      ...new Map(
        canonical.map((line) => [
          `${line.repertoireId}:${canonicalFenKey(line.startingFen)}:${line.moves.join(" ")}`,
          line,
        ]),
      ).values(),
    ];
  }, [backendLines, imported]);
  const repertoires = [
    ...new Map(
      availableLines.map((line) => [
        line.repertoireId,
        { id: line.repertoireId, name: line.repertoireName, side: line.side },
      ]),
    ).values(),
  ];
  const selectedRepertoire =
    repertoires.find((item) => item.id === activeRepertoire) ?? repertoires[0];
  const lineMatches = availableLines.filter(
    (line) =>
      (!selectedRepertoire || line.repertoireId === selectedRepertoire.id) &&
      canonicalFenKey(line.startingFen) === canonicalFenKey(startingFen) &&
      playedUci.every((move, index) => line.moves[index] === move),
  );
  const coveredReplies = new Set<UciMove>(
    lineMatches.flatMap((line) => {
      const uci = line.moves[cursor];
      return uci ? [uci] : [];
    }),
  );
  const positionIndex = useMemo(
    () =>
      indexRepertoirePositions(
        availableLines.filter(
          (line) =>
            !selectedRepertoire || line.repertoireId === selectedRepertoire.id,
        ),
      ),
    [availableLines, selectedRepertoire],
  );
  const similarPositions = useMemo(
    () =>
      positionIndex
        .map((position) => ({
          ...position,
          distance: chessPositionDistance(fen, position.fen),
        }))
        .filter(
          (position): position is typeof position & { distance: number } =>
            position.distance !== undefined &&
            position.distance <= 2 &&
            position.nextUci !== undefined,
        )
        .sort(
          (left, right) =>
            left.distance - right.distance || left.ply - right.ply,
        )
        .filter(
          (position, index, all) =>
            all.findIndex(
              (other) =>
                other.fen === position.fen &&
                other.nextUci === position.nextUci,
            ) === index,
        )
        .slice(0, 8),
    [fen, positionIndex],
  );

  function rememberToggle(
    key: string,
    value: boolean,
    setter: Dispatch<SetStateAction<boolean>>,
  ) {
    localStorage.setItem(key, String(value));
    setter(value);
  }

  useEffect(() => {
    if (!usesLocalApi()) return;
    void fetch(`${API_URL}/api/repertoire/lines`)
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((value) => {
        const body = value as {
          lines: Array<{
            id: string;
            repertoire_id: string;
            repertoire_name: string;
            name: string;
            trained_color: "white" | "black";
            start_fen: string;
            moves: string[];
          }>;
        };
        setBackendLines(
          body.lines.map((line) => ({
            id: asLineId(line.id),
            repertoireId: asRepertoireId(line.repertoire_id),
            repertoireName: line.repertoire_name,
            title: line.name,
            side: line.trained_color === "black" ? "black" : "white",
            startingFen: asFenString(line.start_fen),
            moves: line.moves.map(asSanMove),
          })),
        );
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedRepertoire) return;
    localStorage.setItem(
      `tempo-active-repertoire-${selectedRepertoire.side}`,
      selectedRepertoire.id,
    );
  }, [orientation, selectedRepertoire]);

  useEffect(() => {
    const activeRepertoireByColor: BuilderSession["activeRepertoireByColor"] = {
      white: localStorage.getItem("tempo-active-repertoire-white")
        ? asRepertoireId(localStorage.getItem("tempo-active-repertoire-white")!)
        : undefined,
      black: localStorage.getItem("tempo-active-repertoire-black")
        ? asRepertoireId(localStorage.getItem("tempo-active-repertoire-black")!)
        : undefined,
      ...(selectedRepertoire
        ? { [selectedRepertoire.side]: selectedRepertoire.id }
        : {}),
    };
    const session: BuilderSession = {
      version: 1,
      activeRepertoireId:
        selectedRepertoire?.id ??
        (activeRepertoire ? asRepertoireId(activeRepertoire) : undefined),
      activeRepertoireByColor,
      orientation,
      startingFen: asFenString(String(startingFen)),
      history,
      cursor: Math.min(cursor, history.length),
      branchStart,
    };
    localStorage.setItem("tempo-builder-session", JSON.stringify(session));
  }, [
    activeRepertoire,
    branchStart,
    cursor,
    history,
    orientation,
    selectedRepertoire,
    startingFen,
  ]);

  useEffect(() => {
    let active = true;
    const repertoireId = selectedRepertoire?.id;
    if (!repertoireId) {
      queueMicrotask(() => setAnnotation(undefined));
      return;
    }
    void loadPositionAnnotation(repertoireId, fen).then((value) => {
      if (active) {
        setAnnotation(
          value ?? {
            repertoireId,
            fenKey: asFenKey(canonicalFenKey(fen)),
            comment: "",
            arrows: [],
            squares: [],
            updatedAt: asIsoDateString(""),
          },
        );
        setAnnotationStatus("");
      }
    });
    return () => {
      active = false;
    };
  }, [fen, selectedRepertoire?.id]);

  const updateAnnotationShapes = useCallback((nextShapes: DrawShape[]) => {
    const parts = shapesToAnnotationParts(nextShapes);
    setAnnotation((current) => (current ? { ...current, ...parts } : current));
    setAnnotationStatus("Unsaved changes");
  }, []);

  async function persistAnnotation() {
    if (!annotation) return;
    try {
      const saved = await savePositionAnnotation(annotation, fen);
      setAnnotation(saved);
      setAnnotationStatus("Saved");
    } catch {
      setAnnotationStatus("Could not save — try again");
    }
  }

  async function findTranspositions() {
    transpositionController.current?.abort();
    const controller = new AbortController();
    transpositionController.current = controller;
    setTranspositionState("loading");
    setTranspositions([]);
    try {
      const horizon = Number(
        localStorage.getItem("tempo-maia-transposition-plies") ?? "4",
      );
      const results = await searchMaiaTranspositions({
        startFen: fen,
        targets: positionIndex,
        horizon,
        analyze: (positionFen) => analyzeWithMaia(positionFen, Number(maiaElo)),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setTranspositions(results);
        setTranspositionState("ready");
      }
    } catch {
      if (!controller.signal.aborted) setTranspositionState("error");
    }
  }

  const flipBuilder = useCallback(() => {
    setOrientation((current) => {
      const next = current === "white" ? "black" : "white";
      localStorage.setItem("tempo-builder-orientation", next);
      return next;
    });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const code = params.get("code");
    if (!code) return;
    const verifier = sessionStorage.getItem("tempo-lichess-verifier");
    const expectedState = sessionStorage.getItem("tempo-lichess-state");
    if (!verifier || params.get("state") !== expectedState) {
      queueMicrotask(() => setExplorerState("error"));
      return;
    }
    const redirectUri = `${location.origin}${location.pathname}`;
    fetch("https://lichess.org/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        code_verifier: verifier,
        redirect_uri: redirectUri,
        client_id: "tempo.local.chess.trainer",
      }),
    })
      .then((response) =>
        response.ok
          ? response.json()
          : Promise.reject(new Error("Lichess connection failed")),
      )
      .then((value) => {
        const data = value as { access_token: string };
        sessionStorage.setItem("tempo-lichess-token", data.access_token);
        setLichessToken(data.access_token);
        window.history.replaceState({}, "", redirectUri);
      })
      .catch(() => setExplorerState("error"));
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLSelectElement ||
        event.target instanceof HTMLTextAreaElement
      )
        return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setCursor((value) => Math.max(0, value - 1));
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setCursor((value) => Math.min(history.length, value + 1));
      }
      if (event.key === "Home") {
        event.preventDefault();
        setCursor(0);
      }
      if (event.key === "End") {
        event.preventDefault();
        setCursor(history.length);
      }
      if (event.key === "Escape" && isSearchOpen) {
        event.preventDefault();
        setIsSearchOpen(false);
      }
      if (event.key === "ArrowDown" && isSearchOpen) {
        event.preventDefault();
        setCurrentSearchIndex((value) =>
          Math.min(Math.max(0, lineMatches.length - 1), value + 1),
        );
      }
      if (event.key === "ArrowUp" && isSearchOpen) {
        event.preventDefault();
        setCurrentSearchIndex((value) => Math.max(0, value - 1));
      }
      if (
        event.key === "Enter" &&
        isSearchOpen &&
        lineMatches[currentSearchIndex]
      ) {
        event.preventDefault();
        setIsSearchOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    flipBuilder,
    history.length,
    lineMatches,
    currentSearchIndex,
    isSearchOpen,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setExplorerMoves([]);
      setMastersMoves([]);
      if (!explorerOn || !lichessToken) {
        setExplorerState(lichessToken ? "ready" : "auth");
        return;
      }
      setExplorerState("loading");
      const headers = { Authorization: `Bearer ${lichessToken}` };
      const base = "https://explorer.lichess.org";
      Promise.all([
        fetch(
          `${base}/lichess?variant=standard&speeds=${explorerSpeeds}&ratings=${explorerRatings}&fen=${encodeURIComponent(fen)}`,
          { signal: controller.signal, headers },
        ),
        fetch(
          `${base}/masters?variant=standard&fen=${encodeURIComponent(fen)}`,
          {
            signal: controller.signal,
            headers,
          },
        ),
      ])
        .then(async ([lichess, masters]) => {
          if (!lichess.ok || !masters.ok)
            throw new Error("Explorer request failed");
          return Promise.all([lichess.json(), masters.json()]);
        })
        .then((value) => {
          if (controller.signal.aborted) return;
          const [human, masters] = value as [
            { moves?: RawExplorerMove[] },
            { moves?: RawExplorerMove[] },
          ];
          setExplorerMoves(adaptExplorerMoves(fen, human.moves ?? []));
          setMastersMoves(adaptExplorerMoves(fen, masters.moves ?? []));
          setExplorerState("ready");
        })
        .catch((error) => {
          if (!controller.signal.aborted && error.name !== "AbortError") setExplorerState("error");
        });
    });
    return () => controller.abort();
  }, [fen, explorerOn, lichessToken, explorerRatings, explorerSpeeds]);

  useEffect(() => {
    let current = true;
    queueMicrotask(() => {
      if (!current) return;
      if (!isStockfishOn) {
        setStockfishState("off");
        setStockfishMoves([]);
        return;
      }
      setStockfishMoves([]);
      setStockfishState("loading");
      analyzeWithStockfish(fen)
        .then((moves) => {
          if (current) {
            setStockfishMoves(adaptEngineMoves(fen, moves));
            setStockfishState("ready");
          }
        })
        .catch((error) => {
          console.error("Stockfish 19:", error);
          if (current) setStockfishState("error");
        });
    });
    return () => {
      current = false;
    };
  }, [fen, isStockfishOn]);

  useEffect(() => {
    let current = true;
    queueMicrotask(() => {
      if (!current) return;
      if (!maiaOn) {
        setMaiaState("off");
        setMaiaMoves([]);
        return;
      }
      setMaiaMoves([]);
      setMaiaProgress(0);
      setMaiaState("loading");
      analyzeWithMaia(fen, Number(maiaElo), setMaiaProgress)
        .then((moves) => {
          if (current) {
            setMaiaMoves(adaptEngineMoves(fen, moves));
            setMaiaState("ready");
          }
        })
        .catch((error) => {
          console.error("Maia 3:", error);
          if (current) setMaiaState("error");
        });
    });
    return () => {
      current = false;
    };
  }, [fen, maiaElo, maiaOn]);

  function playMove(from: Square, to: Square) {
    const chess = new Chess(fen);
    try {
      const move = chess.move({ from, to, promotion: "q" });
      setStockfishMoves([]);
      setExplorerMoves([]);
      setMastersMoves([]);
      setMaiaMoves([]);
      setHoveredMove(null);
      const uci = asUciMove(`${move.from}${move.to}${move.promotion ?? ""}`);
      if (!coveredReplies.has(asUciMove(uci)) && branchStart === null)
        setBranchStart(cursor);
      setHistory((current) => [
        ...current.slice(0, cursor),
        { san: asSanMove(move.san), uci, fen: asFenString(chess.fen()) },
      ]);
      setCursor((value) => value + 1);
    } catch {
      /* Chessground only offers legal destinations. */
    }
  }

  function playUci(uci: string) {
    playMove(uci.slice(0, 2) as Square, uci.slice(2, 4) as Square);
  }
  async function saveBranch() {
    if (branchStart === null || history.length <= branchStart) return;
    const moves = history.slice(0, cursor).map((move) => move.uci);
    if (usesLocalApi()) {
      if (!selectedRepertoire) {
        setBranchNote("Choose a repertoire before saving.");
        return;
      }
      try {
        const response = await fetch(`${API_URL}/api/repertoire/branches`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            repertoire_id: selectedRepertoire.id,
            starting_fen: startingFen,
            moves,
            trained_color: selectedRepertoire.side,
            name: history
              .slice(0, cursor)
              .map((move) => move.san)
              .join(" "),
          }),
        });
        const result = (await response.json()) as {
          id: string;
          detail?: string;
        };
        if (!response.ok) throw new Error(result.detail);
        setBackendLines((current) => [
          ...current.filter((line) => line.id !== result.id),
          {
            id: asLineId(result.id),
            repertoireId: selectedRepertoire.id,
            repertoireName: selectedRepertoire.name,
            title: "Branch",
            startingFen: asFenString(String(startingFen)),
            moves,
            side: selectedRepertoire.side,
          },
        ]);
        setBranchNote("Saved");
        setBranchStart(null);
      } catch (error) {
        setBranchNote(
          error instanceof Error
            ? error.message
            : "Could not save. Retry with the local service running.",
        );
      }
      return;
    }
    const stored = JSON.parse(
      localStorage.getItem("tempo-saved-branches") ?? "[]",
    ) as string[][];
    if (!stored.some((line) => line.join(" ") === moves.join(" ")))
      stored.push(moves);
    localStorage.setItem("tempo-saved-branches", JSON.stringify(stored));
    setBranchNote("Draft saved in this demo browser");
    setBranchStart(null);
  }

  const target = coverageTarget / 100;
  const explorerCandidates = candidatesThrough(
    explorerMoves,
    (move) => move.white + move.draws + move.black,
    target,
  ).filter((move) => !coveredReplies.has(move.uci));
  const mastersCandidates = candidatesThrough(
    mastersMoves,
    (move) => move.white + move.draws + move.black,
    target,
  ).filter((move) => !coveredReplies.has(move.uci));
  const maiaCandidates = candidatesThrough(
    maiaMoves,
    (move) => move.probability ?? 0,
    target,
  ).filter((move) => !coveredReplies.has(move.uci));
  const explorerTotal = explorerMoves.reduce(
    (sum, move) => sum + move.white + move.draws + move.black,
    0,
  );
  const explorerCovered = explorerMoves
    .filter((move) => coveredReplies.has(move.uci))
    .reduce((sum, move) => sum + move.white + move.draws + move.black, 0);
  const maiaCovered = maiaMoves
    .filter((move) => coveredReplies.has(move.uci))
    .reduce((sum, move) => sum + (move.probability ?? 0), 0);
  const topEngine = stockfishMoves[0];
  const engineCandidates = stockfishMoves
    .filter((move, index) => {
      if (index >= 5) return false;
      if (topEngine?.mate !== undefined) return move.mate !== undefined;
      if (topEngine?.cp === undefined || move.cp === undefined)
        return index === 0;
      return topEngine.cp - move.cp <= engineWindowCp;
    })
    .filter((move) => !coveredReplies.has(move.uci));
  const arrowSources = new Map<UciMove, Set<string>>();
  for (const move of explorerCandidates)
    arrowSources.set(
      move.uci,
      new Set([...(arrowSources.get(move.uci) ?? []), "L"]),
    );
  for (const move of mastersCandidates)
    arrowSources.set(
      move.uci,
      new Set([...(arrowSources.get(move.uci) ?? []), "D"]),
    );
  for (const move of engineCandidates)
    arrowSources.set(
      move.uci,
      new Set([...(arrowSources.get(move.uci) ?? []), "S"]),
    );
  for (const move of maiaCandidates)
    arrowSources.set(
      move.uci,
      new Set([...(arrowSources.get(move.uci) ?? []), "M"]),
    );
  for (const move of coveredReplies)
    arrowSources.set(move, new Set([...(arrowSources.get(move) ?? []), "R"]));
  const trainedTurn =
    new Chess(fen).turn() ===
    (selectedRepertoire?.side === "black" ? "b" : "w");
  const repertoireMoves = [...coveredReplies].map((uci) => {
    return { uci, san: asSanMove(sanForUci(fen, uci) ?? uci) };
  });
  const practicalMoves =
    arrowMetric === "masters" ? mastersMoves : explorerMoves;
  const practicalScores = new Map(
    practicalMoves.map((move) => {
      const total = move.white + move.draws + move.black;
      const won = orientation === "white" ? move.white : move.black;
      return [
        move.uci,
        total >= 100 ? (won + move.draws / 2) / total : undefined,
      ];
    }),
  );
  const bestPractical = Math.max(
    0,
    ...[...practicalScores.values()].filter(
      (value): value is number => value !== undefined,
    ),
  );
  const hoveredShape = hoveredMove
    ? ([asUciMove(hoveredMove), new Set([""])] as const)
    : undefined;
  const shapes: DrawShape[] = (
    hoveredShape ? [hoveredShape] : [...arrowSources.entries()].slice(0, 9)
  ).map(([uci, sources]) => {
    const engineIndex = engineCandidates.findIndex((move) => move.uci === uci);
    const practical = practicalScores.get(uci);
    const graded =
      arrowMetric === "stockfish"
        ? engineIndex === 0
          ? "green"
          : engineIndex > 0
            ? "blue"
            : "red"
        : practical === undefined
          ? "blue"
          : practical === bestPractical
            ? "green"
            : bestPractical - practical <= 0.05
              ? "blue"
              : "red";
    const brush = sources.has("R") ? "yellow" : !trainedTurn ? "blue" : graded;
    return {
      orig: uci.slice(0, 2) as Key,
      dest: uci.slice(2, 4) as Key,
      brush,
      label: hoveredMove
        ? undefined
        : {
            text:
              [...sources].filter((source) => source !== "R").join("·") || "R",
          },
    };
  });

  function reset() {
    setHistory([]);
    setCursor(0);
    const nextStart = availableLines.find(
      (line) => line.repertoireId === selectedRepertoire?.id,
    )?.startingFen;
    if (nextStart) setStartingFen(nextStart);
  }
  function disconnectLichess() {
    sessionStorage.removeItem("tempo-lichess-token");
    setLichessToken("");
    setExplorerMoves([]);
  }

  return (
    <section className="analysis-page" id="builder">
      <div className="analysis-heading compact-analysis">
        <h1>Builder</h1>
        <div className="analysis-switches">
          <select
            aria-label="Active repertoire"
            value={selectedRepertoire?.id ?? ""}
            onChange={(event) => {
              const selected = repertoires.find(
                (item) => item.id === event.target.value,
              );
              if (!selected) return;
              const side = selected.side.toLowerCase() as "white" | "black";
              setOrientation(side);
              setActiveRepertoire(selected.id);
              localStorage.setItem(
                `tempo-active-repertoire-${side}`,
                selected.id,
              );
              localStorage.setItem("tempo-builder-orientation", side);
              const candidateLines = availableLines.filter(
                (line) => line.repertoireId === selected.id,
              );
              const compatible = candidateLines.some(
                (line) =>
                  canonicalFenKey(line.startingFen) ===
                    canonicalFenKey(startingFen) &&
                  playedUci.every((move, index) => line.moves[index] === move),
              );
              if (!compatible) {
                setStartingFen(candidateLines[0]?.startingFen ?? STANDARD_FEN);
                setHistory([]);
                setCursor(0);
                setBranchStart(null);
                setBranchNote("Position reset for the selected repertoire");
              }
            }}
          >
            <option value="" disabled>
              Choose repertoire
            </option>
            {repertoires.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.side}
              </option>
            ))}
          </select>
          <button title="Flip board (F)" onClick={flipBuilder}>
            ⇅ {orientation === "white" ? "White" : "Black"}
          </button>
          <button
            className={isStockfishOn ? "on" : ""}
            onClick={() =>
              rememberToggle(
                "tempo-stockfish-on",
                !isStockfishOn,
                setIsStockfishOn,
              )
            }
          >
            <i /> Stockfish 19
          </button>
          <button
            className={maiaOn ? "on" : ""}
            onClick={() => rememberToggle("tempo-maia-on", !maiaOn, setMaiaOn)}
          >
            <i /> Maia 3
          </button>
        </div>
      </div>
      <div className="analysis-layout">
        <div className="analysis-board-column">
          <Chessboard
            fen={fen}
            lastMove={lastMove}
            locked={false}
            showHint={false}
            theme={theme}
            pieceSet={pieceSet}
            shapes={shapes}
            drawnShapes={annotationToShapes(annotation)}
            onDrawnShapesChange={updateAnnotationShapes}
            onMove={playMove}
            orientation={orientation}
            onFlip={flipBuilder}
          />
          <div className="arrow-legend">
            <span>
              <i className="known" /> Covered
            </span>
            <span>
              <i className="candidate" /> Gap
            </span>
            <span tabIndex={0} title="R: already in your repertoire">
              <b>R</b> Repertoire
            </span>
            <span tabIndex={0} title="L: Lichess opening explorer">
              <b>L</b> Lichess
            </span>
            <span tabIndex={0} title="D: Lichess Masters database">
              <b>D</b> Masters
            </span>
            <span tabIndex={0} title="S: Stockfish engine line">
              <b>S</b> Stockfish
            </span>
            <span tabIndex={0} title="M: Maia human-likelihood model">
              <b>M</b> Maia
            </span>
          </div>
          <div className="board-tools">
            <button
              onClick={() => setCursor((value) => Math.max(0, value - 1))}
              disabled={!cursor}
            >
              ← <span>Back</span>
            </button>
            <button
              onClick={() =>
                setCursor((value) => Math.min(history.length, value + 1))
              }
              disabled={cursor === history.length}
            >
              → <span>Forward</span>
            </button>
            <button onClick={reset}>
              ↻ <span>Reset</span>
            </button>
            <a
              href={`https://lichess.org/analysis/standard/${encodeURIComponent(fen)}`}
              target="_blank"
              rel="noreferrer"
            >
              ↗ <span>Open in Lichess</span>
            </a>
          </div>
          <div className="analysis-moves">
            <span>
              {history.length
                ? history.map((move, index) => (
                    <button
                      className={index < cursor ? "shown" : ""}
                      key={`${move.uci}-${index}`}
                      onClick={() => setCursor(index + 1)}
                    >
                      {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}
                      {move.san}
                    </button>
                  ))
                : "Make a move to search your repertoire"}
            </span>
            <small>←/→ move · Home/End jump</small>
            <button onClick={() => navigator.clipboard?.writeText(fen)}>
              Copy FEN
            </button>
          </div>
          <div className="branch-editor">
            <span>
              <b>
                {branchStart === null
                  ? "Branch editor"
                  : `Drafting from ply ${branchStart}`}
              </b>
              <small>
                {branchNote ||
                  "Select an opponent move, then play your response and any continuation."}
              </small>
            </span>
            {branchStart === null ? (
              <button
                onClick={() => {
                  setBranchStart(cursor);
                  setBranchNote("");
                }}
              >
                ＋ Add branch here
              </button>
            ) : (
              <>
                <button className="save" onClick={saveBranch}>
                  Save branch
                </button>
                <button
                  onClick={() => {
                    setHistory((h) => h.slice(0, branchStart));
                    setCursor(branchStart);
                    setBranchStart(null);
                  }}
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
        <aside className="analysis-sidebar">
          {availableLines.some(
            (line) => line.validation?.diagnostics.length,
          ) && (
            <section
              className="analysis-panel import-diagnostics"
              role="status"
            >
              <div className="panel-heading">
                <div>
                  <span>Import diagnostics</span>
                  <strong>Some line data was skipped safely</strong>
                </div>
              </div>
              {availableLines.flatMap((line) =>
                (line.validation?.diagnostics ?? []).map((diagnostic) => (
                  <p key={`${line.id}-${diagnostic.ply}-${diagnostic.move}`}>
                    {line.repertoireName}: {diagnostic.message} at ply{" "}
                    {diagnostic.ply + 1}
                  </p>
                )),
              )}
            </section>
          )}
          <section className="analysis-panel repertoire-panel">
            <div className="panel-heading">
              <div>
                <span>Active repertoire</span>
                <strong>
                  {selectedRepertoire?.name ?? "No repertoire selected"}
                </strong>
              </div>
            </div>
            {repertoireMoves.length ? (
              <CandidateMovesTable
                moves={repertoireMoves}
                covered={coveredReplies}
                detail="score"
                onPlay={playUci}
                onHover={setHoveredMove}
              />
            ) : (
              <p className="panel-message">
                No saved response at this position.
              </p>
            )}
          </section>
          <section className="analysis-panel coverage-panel">
            <div className="panel-heading">
              <div>
                <span>Coverage</span>
                <strong>Likely {coverageTarget}% · change in Settings</strong>
              </div>
            </div>
            <div className="coverage-summary">
              <div>
                <span>Lichess coverage</span>
                <strong>
                  {explorerTotal
                    ? Math.round((explorerCovered / explorerTotal) * 100)
                    : "—"}
                  %
                </strong>
              </div>
              <div>
                <span>Maia coverage</span>
                <strong>
                  {maiaMoves.length ? Math.round(maiaCovered * 100) : "—"}%
                </strong>
              </div>
              <div>
                <span>Responses saved</span>
                <strong>{coveredReplies.size}</strong>
              </div>
            </div>
          </section>
          <section className="analysis-panel annotation-panel">
            <div className="panel-heading">
              <div>
                <span>Position note</span>
                <strong>Shown only after a mistake</strong>
              </div>
            </div>
            <textarea
              aria-label="Position comment"
              placeholder="Add a reminder for this exact position…"
              value={annotation?.comment ?? ""}
              onChange={(event) => {
                const comment = event.target.value;
                setAnnotation((current) =>
                  current ? { ...current, comment } : current,
                );
                setAnnotationStatus("Unsaved changes");
              }}
            />
            <small>
              Right-drag an arrow or right-click a square on the board to add
              graphical notes.
            </small>
            <div className="annotation-actions">
              <span role="status">{annotationStatus}</span>
              <button
                onClick={() => {
                  setAnnotation((current) =>
                    current
                      ? { ...current, comment: "", arrows: [], squares: [] }
                      : current,
                  );
                  setAnnotationStatus("Unsaved changes");
                }}
              >
                Clear
              </button>
              <button className="save" onClick={() => void persistAnnotation()}>
                Save note
              </button>
            </div>
          </section>
          <section className="analysis-panel similarity-panel">
            <div className="panel-heading">
              <div>
                <span>Consistency</span>
                <strong>Similar repertoire positions</strong>
              </div>
              <b>{similarPositions.length}</b>
            </div>
            {similarPositions.length ? (
              <div className="similar-position-list">
                {similarPositions.map((position) => (
                  <button
                    key={`${position.lineId}-${position.ply}-${position.nextUci}`}
                    onClick={() =>
                      position.nextUci && playUci(position.nextUci)
                    }
                  >
                    <span>
                      {position.distance === 0
                        ? "Exact"
                        : `${position.distance} relocation${position.distance === 1 ? "" : "s"}`}
                    </span>
                    <strong>
                      {position.nextUci
                        ? (sanForUci(fen, position.nextUci) ?? position.nextUci)
                        : "Endpoint"}
                    </strong>
                    <small>
                      {position.repertoireName} · ply {position.ply}
                    </small>
                  </button>
                ))}
              </div>
            ) : (
              <p className="panel-message">
                No compatible distance-one or distance-two positions.
              </p>
            )}
          </section>
          <section className="analysis-panel transposition-panel">
            <div className="panel-heading">
              <div>
                <span>Maia paths</span>
                <strong>Likely transpositions</strong>
              </div>
            </div>
            <button
              className="find-transpositions"
              disabled={!maiaOn || transpositionState === "loading"}
              onClick={() => void findTranspositions()}
            >
              {transpositionState === "loading"
                ? "Searching…"
                : "Find likely paths"}
            </button>
            {transpositionState === "error" && (
              <p className="panel-message error">
                Maia could not complete this search.
              </p>
            )}
            {transpositionState === "ready" && !transpositions.length && (
              <p className="panel-message">
                No likely transposition within the configured horizon.
              </p>
            )}
            {transpositions.map((result) => (
              <button
                className="transposition-result"
                key={`${result.lineId}-${result.ply}-${result.path.join("-")}`}
                onClick={() => result.path[0] && playUci(result.path[0])}
              >
                <strong>
                  {result.distance === 0
                    ? "Exact transposition"
                    : `Distance ${result.distance}`}
                </strong>
                <span>{result.path.join(" · ")}</span>
                <small>
                  {Math.round(result.probability * 100)}% path ·{" "}
                  {result.repertoireName}
                </small>
              </button>
            ))}
          </section>
          <button
            className="analysis-panel repertoire-results position-preview"
            onClick={() => {
              setCurrentSearchIndex(0);
              setIsSearchOpen(true);
            }}
          >
            <div className="panel-heading">
              <div>
                <span>Position search</span>
                <strong>
                  {lineMatches.length
                    ? `${lineMatches.length} repertoire ${lineMatches.length === 1 ? "match" : "matches"}`
                    : "Repertoire gap"}
                </strong>
              </div>
              <b className={lineMatches.length ? "covered" : "gap"}>
                {lineMatches.length ? "Browse" : "Add"}
              </b>
            </div>
            {lineMatches.slice(0, 2).map((line) => (
              <span className="line-result" key={line.id}>
                <strong>{lineMoveName(line)}</strong>
              </span>
            ))}
          </button>
          <section className="analysis-panel explorer-panel">
            <div className="panel-heading">
              <div>
                <span>Lichess opening explorer</span>
                <strong>Human games · {coverageTarget}% set</strong>
              </div>
              <button
                className={`tiny-switch${explorerOn ? " on" : ""}`}
                onClick={() =>
                  rememberToggle(
                    "tempo-explorer-on",
                    !explorerOn,
                    setExplorerOn,
                  )
                }
              >
                {explorerOn ? "Live" : "Off"}
              </button>
            </div>
            {!explorerOn ? (
              <p className="panel-message">Explorer is paused.</p>
            ) : explorerState === "auth" ? (
              <div className="connect-panel">
                <p>Connect Lichess to load Explorer data.</p>
                <button onClick={connectLichess}>Connect Lichess</button>
              </div>
            ) : explorerState === "loading" ? (
              <p className="panel-message">Loading Lichess data…</p>
            ) : explorerState === "error" ? (
              <div className="connect-panel">
                <p>The Lichess connection needs to be refreshed.</p>
                <button onClick={connectLichess}>Reconnect</button>
              </div>
            ) : (
              <>
                <div className="source-status">
                  <span>Connected</span>
                  <button onClick={disconnectLichess}>Disconnect</button>
                </div>
                <CandidateMovesTable
                  moves={explorerCandidates.map((move) => ({
                    ...move,
                    probability: explorerTotal
                      ? (move.white + move.draws + move.black) / explorerTotal
                      : 0,
                  }))}
                  covered={coveredReplies}
                  detail="results"
                  turn={new Chess(fen).turn() === "w" ? "white" : "black"}
                  totalGames={explorerTotal}
                  onPlay={playUci}
                  onHover={setHoveredMove}
                />
              </>
            )}
          </section>
          <section className="analysis-panel explorer-panel">
            <div className="panel-heading">
              <div>
                <span>Masters database</span>
                <strong>Master games · {coverageTarget}% set</strong>
              </div>
            </div>
            <CandidateMovesTable
              moves={mastersCandidates}
              covered={coveredReplies}
              detail="results"
              turn={new Chess(fen).turn() === "w" ? "white" : "black"}
              totalGames={mastersMoves.reduce(
                (sum, move) => sum + move.white + move.draws + move.black,
                0,
              )}
              onPlay={playUci}
              onHover={setHoveredMove}
            />
          </section>
          <section className="analysis-panel engine-panel">
            <div className="panel-heading">
              <div>
                <span>Stockfish 19</span>
                <strong>Engine lines</strong>
              </div>
              <b className={`engine-badge ${stockfishState}`}>
                {stockfishState === "loading"
                  ? "Analyzing…"
                  : stockfishState === "ready"
                    ? "Local"
                    : stockfishState === "error"
                      ? "Could not start"
                      : "Off"}
              </b>
            </div>
            {stockfishState === "ready" && (
              <CandidateMovesTable
                moves={engineCandidates}
                covered={coveredReplies}
                detail="score"
                onPlay={playUci}
                onHover={setHoveredMove}
              />
            )}
          </section>
          <section className="analysis-panel engine-panel">
            <div className="panel-heading">
              <div>
                <span>Maia 3</span>
                <strong>Likely moves at {maiaElo}</strong>
              </div>
            </div>
            {maiaState === "loading" ? (
              <p className="panel-message">
                {maiaProgress
                  ? `Downloading model · ${maiaProgress}%`
                  : "Initializing local Maia…"}
              </p>
            ) : maiaState === "error" ? (
              <p className="panel-message error">
                Maia could not start. Toggle it off and on to retry.
              </p>
            ) : maiaState === "ready" ? (
              <CandidateMovesTable
                moves={maiaCandidates}
                covered={coveredReplies}
                detail="probability"
                onPlay={playUci}
                onHover={setHoveredMove}
              />
            ) : (
              <p className="panel-message">Maia is off.</p>
            )}
          </section>
        </aside>
      </div>
      {isSearchOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={() => setIsSearchOpen(false)}
        >
          <section
            className="position-search-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Position search"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <CloseButton
              onClose={() => setIsSearchOpen(false)}
              ariaLabel="Close position search"
            />
            <h2>Position search</h2>
            <p>{lineMatches.length} matching branches</p>
            <div className="position-search-list">
              {lineMatches.map((line, index) => (
                <button
                  className={index === currentSearchIndex ? "active" : ""}
                  key={line.id}
                  onMouseEnter={() => setCurrentSearchIndex(index)}
                  onClick={() => setIsSearchOpen(false)}
                >
                  <span>
                    {line.side} · {line.repertoireName}
                  </span>
                  <strong>{lineMoveName(line)}</strong>
                  <small>
                    {line.moves.slice(cursor, cursor + 4).join(" · ") ||
                      "Exact line endpoint"}
                  </small>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
