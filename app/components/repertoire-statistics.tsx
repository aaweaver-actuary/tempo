import { useEffect, useState } from "react";
import { z } from "zod";
import { Surface } from "./ui";
import { Button } from "./buttons/BaseButton";
import { SelectInput } from "./inputs/SelectInput";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import { Notice } from "./task-tabs";
import { API_URL } from "../const";

const count = z.number().int().nonnegative();
const ratio = z.number().min(0).max(1).nullable();
const statisticsSchema = z.object({
  window: z.enum(["30d", "90d", "all"]),
  graph_updated_at: z.string().nullable(),
  graph_state: z.enum(["ready", "refreshing", "failed"]),
  game_state: z.enum(["ready", "refreshing", "failed"]),
  prefix: z.object({
    total: count,
    active: count,
    studied: count,
    unseen: count,
    locked: count,
    paused: count,
  }),
  cards: z.object({
    total: count,
    new: count,
    learning: count,
    mature: count,
    locked: count,
    difficult: count,
    due_today: count,
    due_next_seven_days: count,
  }),
  study: z.object({ correct: count, attempts: count, accuracy: ratio }),
  games: z.object({
    matched: count,
    correct: count,
    decisions: count,
    adherence: ratio,
    wins: count,
    draws: count,
    losses: count,
    positions_seen: count,
    positions_total: count,
  }),
  unlocks: z.array(
    z.object({
      card_id: z.string(),
      parent_card_id: z.string(),
      line_name: z.string(),
      parent_due_date: z.string(),
      earliest_unlock_date: z.string().nullable(),
      status: z.enum([
        "forecast",
        "ready",
        "waiting_practice",
        "paused",
        "waiting_introduction",
        "unavailable",
      ]),
    }),
  ),
});
const positionSchema = z.object({
  fen_key: z.string(),
  fen: z.string(),
  games: count,
  encounters: count,
  correct: count,
  missed: count,
  last_seen_at: z.string(),
  expected_moves: z.array(z.string()),
  played_moves: z.array(z.object({ move_uci: z.string(), count })),
  card_id: z.string().nullable(),
  sample_game_id: z.string().nullable(),
  sample_ply: count.nullable(),
});
const positionsSchema = z.object({
  positions: z.array(positionSchema),
  total: count,
  next_cursor: z.string().nullable(),
});
type Window = "30d" | "90d" | "all";
type Sort = "attention" | "frequency";
type Position = z.infer<typeof positionSchema>;

async function fetchStatisticsData<T>(url: string, schema: z.ZodType<T>): Promise<T> {
  const requestController = new AbortController();
  const timeout = globalThis.setTimeout(() => requestController.abort(), 15000);
  try {
    const response = await fetch(url, { signal: requestController.signal });
    if (!response.ok) throw new Error(`Statistics unavailable (HTTP ${response.status}). Retry when the local service is available.`);
    return schema.parse(await response.json());
  } catch (error) {
    if (requestController.signal.aborted) throw new Error("Statistics request timed out. Retry when the local service is available.");
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

const percentage = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export function RepertoireStatistics({
  repertoireId,
  repertoireName,
  theme,
  pieceSet,
  onBack,
  onShowGamesAtPosition,
}: {
  repertoireId: string;
  repertoireName: string;
  theme: BoardTheme;
  pieceSet: PieceSet;
  onBack: () => void;
  onShowGamesAtPosition: (fen: string, repertoireId: string) => void;
}) {
  const [window, setWindow] = useState<Window>("90d");
  const [sort, setSort] = useState<Sort>("attention");
  const [statistics, setStatistics] = useState<z.infer<
    typeof statisticsSchema
  > | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [selectedFen, setSelectedFen] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const selectedPosition =
    positions.find((position) => position.fen_key === selectedFen) ??
    positions[0];

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setStatistics(null);
      setPositions([]);
      setError("");
    });
    const base = `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/statistics`;
    void Promise.all([
      fetchStatisticsData(`${base}?window=${window}`, statisticsSchema),
      fetchStatisticsData(
        `${base}/positions?window=${window}&sort=${sort}&limit=20`,
        positionsSchema,
      ),
    ])
      .then(([summary, positionPage]) => {
        if (!active) return;
        setStatistics(summary);
        setPositions(positionPage.positions);
        setNextCursor(positionPage.next_cursor);
        setSelectedFen(positionPage.positions[0]?.fen_key ?? null);
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Repertoire statistics unavailable.",
          );
      });
    return () => {
      active = false;
    };
  }, [repertoireId, window, sort, retry]);

  useEffect(() => {
    if (!statistics || statistics.graph_state !== "refreshing" && statistics.game_state !== "refreshing") return;
    const refreshTimer = globalThis.setTimeout(() => setRetry((value) => value + 1), 5000);
    return () => globalThis.clearTimeout(refreshTimer);
  }, [statistics]);

  async function loadMore() {
    if (!nextCursor) return;
    try {
      const next = await fetchStatisticsData(
        `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/statistics/positions?window=${window}&sort=${sort}&limit=20&cursor=${encodeURIComponent(nextCursor)}`,
        positionsSchema,
      );
      setPositions((current) => [...current, ...next.positions]);
      setNextCursor(next.next_cursor);
      setError("");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "More positions unavailable.",
      );
    }
  }

  return (
    <section
      className="library-page repertoire-statistics"
      aria-label={`Statistics for ${repertoireName}`}
    >
      <div className="page-heading compact">
        <div>
          <Button onClick={onBack}>← Repertoires</Button>
          <h1>{repertoireName}</h1>
          <p>Practice, game decisions, and upcoming cards</p>
        </div>
        <label>
          Period{" "}
          <SelectInput
            value={window}
            onChange={(event) => setWindow(event.target.value as Window)}
          >
            <option value="30d">30 days</option>
            <option value="90d">90 days</option>
            <option value="all">Lifetime</option>
          </SelectInput>
        </label>
      </div>
      {error && (
        <Notice error onRetry={() => setRetry((value) => value + 1)}>
          {error}
        </Notice>
      )}
      {!statistics && !error && (
        <p role="status">Loading repertoire statistics…</p>
      )}
      {statistics && (
        <>
          {!statistics.graph_updated_at && (
            <p role="status">Preparing the first published repertoire graph. Statistics will appear after it finishes.</p>
          )}
          {statistics.graph_state === "refreshing" && statistics.graph_updated_at && (
            <p role="status">
              Repertoire graph refreshing; showing the latest published cards.
            </p>
          )}
          {statistics.graph_state === "failed" && (
            <Notice error>Repertoire graph failed. Retry the failed task in Settings → Service status. Latest published cards are shown where available.</Notice>
          )}
          {statistics.game_state === "refreshing" && (
            <p role="status">
              Game comparisons refreshing; game measures may be stale.
            </p>
          )}
          {statistics.game_state === "failed" && (
            <Notice error>Game comparisons failed. Retry the failed task in Settings → Service status. Game measures may be stale.</Notice>
          )}
          <div className="repertoire-statistics-content" hidden={!statistics.graph_updated_at}>
          <div className="repertoire-stats-grid">
            <Surface as="article">
              <h2>Prefix cards</h2>
              <strong>{statistics.prefix.active} active</strong>
              <p>
                {statistics.prefix.studied} studied · {statistics.prefix.unseen}{" "}
                unseen
              </p>
              <small>
                {statistics.prefix.total} total · {statistics.prefix.locked}{" "}
                locked · {statistics.prefix.paused} paused
              </small>
            </Surface>
            <Surface as="article">
              <h2>Practice accuracy</h2>
              <strong>{percentage(statistics.study.accuracy)}</strong>
              <p>
                {statistics.study.correct} correct / {statistics.study.attempts}{" "}
                first daily attempts
              </p>
            </Surface>
            <Surface as="article">
              <h2>Game adherence</h2>
              <strong>{percentage(statistics.games.adherence)}</strong>
              <p>
                {statistics.games.correct} correct /{" "}
                {statistics.games.decisions} decisions ·{" "}
                {statistics.games.matched} matched games
              </p>
              <small>
                {statistics.games.wins} W · {statistics.games.draws} D ·{" "}
                {statistics.games.losses} L
              </small>
            </Surface>
          </div>
          <div className="repertoire-stats-grid">
            <Surface as="article">
              <h2>Card mix</h2>
              <p>
                {statistics.cards.new} new · {statistics.cards.learning}{" "}
                learning · {statistics.cards.mature} mature ·{" "}
                {statistics.cards.locked} locked
              </p>
              <p>
                {statistics.cards.difficult} difficult{" "}
                <small>(also counted in their card state)</small>
              </p>
              <small>
                {statistics.cards.due_today} due today ·{" "}
                {statistics.cards.due_next_seven_days} scheduled over the next
                seven days
              </small>
            </Surface>
            <Surface as="article">
              <h2>Game exposure</h2>
              <strong>
                {statistics.games.positions_seen} /{" "}
                {statistics.games.positions_total}
              </strong>
              <p>trained decision positions reached in primary-matched games</p>
            </Surface>
          </div>
          <Surface as="section">
            <h2>Next unlocks</h2>
            <p>
              One completed parent practice unlocks the next move. Ready cards
              are introduced within your daily new-card limit.
            </p>
            {statistics.unlocks.length === 0 ? (
              <p>No locked cards at the current frontier.</p>
            ) : (
              <ol>
                {statistics.unlocks.map((unlock) => (
                  <li key={`${unlock.card_id}:${unlock.parent_card_id}`}>
                    <strong>{unlock.line_name}</strong> ·{" "}
                    {unlock.status === "ready"
                      ? "Ready for introduction"
                      : unlock.status === "waiting_practice"
                        ? "Waiting for parent practice"
                        : unlock.status === "paused"
                          ? "Paused for repair or validation"
                          : unlock.status === "waiting_introduction"
                            ? "Waiting for parent introduction"
                            : unlock.earliest_unlock_date ?? "Date unavailable"}
                    <small>
                      {" "}
                      Parent card {unlock.parent_card_id.slice(0, 8)} · next due{" "}
                      {unlock.parent_due_date}
                    </small>
                  </li>
                ))}
              </ol>
            )}
          </Surface>
          <Surface as="section">
            <div className="repertoire-position-heading">
              <div>
                <h2>Positions reached in games</h2>
                <p>
                  Trained decisions from primary-matched games. Select a
                  position to inspect it.
                </p>
              </div>
              <label>
                Sort{" "}
                <SelectInput
                  value={sort}
                  onChange={(event) => setSort(event.target.value as Sort)}
                >
                  <option value="attention">Most missed</option>
                  <option value="frequency">Most played</option>
                </SelectInput>
              </label>
            </div>
            {positions.length === 0 ? (
              <p>
                No trained decision positions reached in the selected period.
              </p>
            ) : (
              <div className="repertoire-position-layout">
                <div className="repertoire-position-list" role="list">
                  {positions.map((position) => (
                    <Button
                      key={position.fen_key}
                      className={
                        selectedPosition?.fen_key === position.fen_key
                          ? "selected"
                          : ""
                      }
                      onClick={() => setSelectedFen(position.fen_key)}
                    >
                      {position.encounters} encounters · {position.correct} correct ·{" "}
                      {position.missed} missed ·{" "}
                      {position.expected_moves.join(", ")}
                    </Button>
                  ))}
                  {nextCursor && (
                    <Button onClick={() => void loadMore()}>
                      More positions
                    </Button>
                  )}
                </div>
                {selectedPosition && (
                  <div className="repertoire-position-detail">
                    <div className="repertoire-position-board">
                      <Chessboard
                        fen={selectedPosition.fen}
                        locked
                        showHint={false}
                        theme={theme}
                        pieceSet={pieceSet}
                        onMove={() => undefined}
                        orientation={
                          selectedPosition.fen.split(" ")[1] === "b"
                            ? "black"
                            : "white"
                        }
                      />
                    </div>
                    <p>
                      Expected {selectedPosition.expected_moves.join(", ")} ·{" "}
                      {selectedPosition.games} games · {selectedPosition.encounters} encounters · last reached{" "}
                      {selectedPosition.last_seen_at.slice(0, 10)}
                    </p>
                    <ul>
                      {selectedPosition.played_moves.map((move) => (
                        <li key={move.move_uci}>
                          {move.move_uci}: {move.count}
                        </li>
                      ))}
                    </ul>
                    <Button
                      onClick={() =>
                        onShowGamesAtPosition(
                          selectedPosition.fen,
                          repertoireId,
                        )
                      }
                    >
                      View supporting games
                    </Button>
                  </div>
                )}
              </div>
            )}
          </Surface>
          <p className="repertoire-stats-method">
            Shared cards appear in each linked repertoire, so repertoire card
            totals need not add up. “Difficult” means Tempo’s hard practice
            mode.
          </p>
          </div>
        </>
      )}
    </section>
  );
}
