import { Chess } from "chess.js";
import type { BoardProps, Square } from "@tempo/board-ui";
import { positionsFromMoves } from "../../hooks/use-board-history";
import type { Finding, GameDetail, FirstDepartureSelection, EncounterReceipt } from "../product-api/contracts";

export const findingLabels: Record<Finding["kind"], string> = {
  confirmed_miss: "Confirmed recall miss",
  opponent_gap: "Uncovered opponent reply",
  missing_own_continuation: "Missing own continuation",
  known_unintroduced: "Card not introduced",
  known_unavailable: "Card unavailable",
  conflict: "Conflicting repertoire responses",
  ambiguous: "Ambiguous evidence",
  coverage_end: "End of repertoire coverage",
};

/** Replays every original move, preserving clocks, castling and en passant history. */
export function gamePositions(startingFen: string, moves: readonly string[]): readonly string[] {
  const positions = positionsFromMoves(startingFen, moves);
  if (positions.length !== moves.length + 1) throw new Error("Original game history is incomplete or invalid. Board review is unavailable.");
  return positions;
}

export function boardPosition(
  identity: string, positions: readonly string[], moves: readonly string[], ply: number,
  orientation: BoardProps["orientation"],
): BoardProps {
  const cursor = Math.max(0, Math.min(ply, positions.length - 1));
  const fen = positions[cursor];
  const board = new Chess(fen);
  const previous = moves[cursor - 1];
  return {
    fen, positionIdentity: identity, positionRevision: cursor, orientation,
    turn: board.turn() === "w" ? "white" : "black",
    check: board.isCheck() ? board.turn() === "w" ? "white" : "black" : false,
    mode: "select", readOnly: true, drawingEnabled: false,
    lastMove: previous ? [previous.slice(0, 2) as Square, previous.slice(2, 4) as Square] : undefined,
    ariaLabel: `Original game board, ply ${cursor}`,
  };
}

export function receiptLabel(receipt: GameDetail["receipts"][number] | null | undefined): string {
  if (!receipt) return "No receiving-owner receipt";
  const status = ["conflict", "stale_revision", "withdrawn"].includes(receipt.outcome)
    ? `Feedback not applied: ${receipt.outcome.replaceAll("_", " ")}${receipt.reason ? ` — ${receipt.reason}` : ""}`
    : `Study feedback: ${receipt.status}${receipt.reason ? ` — ${receipt.reason}` : ""}`;
  if (!("admission_status" in receipt)) return status;
  const disposition = receipt.disposition?.replaceAll("_", " ") ?? "unresolved";
  const admission = receipt.admission_status === "available" ? "available for Study" : receipt.admission_status.replaceAll("_", " ");
  return `${status} · Encounter ${receipt.observation_recorded ? "recorded" : "not recorded"} · Disposition: ${disposition} · Admission: ${admission}${receipt.deferred_reason ? ` · Deferred: ${receipt.deferred_reason.replaceAll("_", " ")}` : ""}`;
}

export const departureLabels: Record<FirstDepartureSelection["status"], string> = {
  own_actionable: "Your first repertoire departure",
  own_unmapped: "Your first departure — Study target unresolved",
  opponent_gap: "Opponent departed first",
  coverage_end: "Authored repertoire coverage ended",
  missing_own_continuation: "No authored response at this position",
  unresolved: "Comparison unresolved",
  covered: "No departure in the recorded covered history",
};

/** The complete original effect identity survives physical target remapping. */
export function departureReceipt(detail: GameDetail): GameDetail["receipts"][number] | undefined {
  const selected = detail.first_departure;
  if (selected?.status !== "own_actionable" || !selected.decision) return undefined;
  const matching = detail.receipts.filter(receipt => receipt.key.workspace_id === selected.workspace_id
    && receipt.key.provider === selected.source.provider && receipt.key.game_id === selected.source.game_id
    && receipt.key.occurrence_ply === selected.ply && receipt.key.decision_id === selected.decision?.decision_id
    && receipt.source_revision === selected.source.source_revision
    && (!("source_digest" in receipt) || receipt.source_digest === selected.source.source_digest));
  const distinct = new Map(matching.map(receipt => [JSON.stringify(receipt), receipt]));
  return distinct.size === 1 ? [...distinct.values()][0] : undefined;
}

export function isEncounterReceipt(receipt: GameDetail["receipts"][number] | undefined): receipt is EncounterReceipt {
  return receipt !== undefined && "admission_status" in receipt;
}

export function departureProgressStage(receipt: GameDetail["receipts"][number] | undefined): string {
  if (!receipt) return "unknown";
  if (!isEncounterReceipt(receipt) || !receipt.progress) return "legacy";
  if (receipt.status === "held" || ["conflict", "stale_revision", "withdrawn"].includes(receipt.outcome)) return "held";
  if (receipt.progress.settlement) return "settled";
  if (receipt.progress.first_issued) return "issued";
  if (receipt.progress.schedule_applied_at) return "again_applied";
  if (receipt.progress.introduction_applied_at) return "introduced";
  return "accepted";
}

export function departureProgress(receipt: GameDetail["receipts"][number] | undefined): string {
  if (!receipt) return "No Study receipt is available; application is not confirmed.";
  if (!isEncounterReceipt(receipt) || !receipt.progress) return `Original effect receipt · ${receiptLabel(receipt)}`;
  const progress = receipt.progress;
  const held = receipt.status === "held" || ["conflict", "stale_revision", "withdrawn"].includes(receipt.outcome);
  if (held) return `Study feedback held: ${receipt.reason ?? receipt.outcome.replaceAll("_", " ")}`;
  const application = progress.scheduling_review_id !== null && progress.schedule_applied_at !== null
    ? "Again applied" : progress.introduction_applied_at !== null ? "Canonical first introduction applied" : null;
  if (progress.settlement) return `${application} · Settled after a genuine Study review`;
  if (progress.first_issued) return `${application} · Issued for Study (new issuance ${progress.first_issued_ordinal})`;
  if (application) return `${application} · Awaiting a new Study issuance`;
  return `Accepted · ${receipt.disposition === "introduced_again" ? "Again" : "First introduction"} pending${receipt.reason ? `: ${receipt.reason.replaceAll("_", " ")}` : ""}`;
}

/** Use the original pre-move FEN for actor, fullmove number and legal notation. */
export function departureMove(positions: readonly string[], ply: number, move: string): string {
  const board = new Chess(positions[ply - 1]);
  const side = board.turn(), fullmove = board.fen().split(" ")[5];
  let notation = move;
  try { notation = board.move(move)?.san ?? move; } catch { /* Accurate UCI remains inspectable. */ }
  return `${fullmove}${side === "b" ? "..." : "."} ${notation}`;
}

/** Feature-owned cancellation. Ignoring AbortSignal cannot publish an obsolete result. */
export class OwnedRequests {
  private requests = new Map<string, AbortController>();

  start(scope: string) {
    this.cancel(scope);
    const controller = new AbortController();
    this.requests.set(scope, controller);
    return {
      signal: controller.signal,
      current: () => this.requests.get(scope) === controller && !controller.signal.aborted,
      finish: () => { if (this.requests.get(scope) === controller) this.requests.delete(scope); },
    };
  }

  cancel(scope: string) {
    this.requests.get(scope)?.abort();
    this.requests.delete(scope);
  }

  cancelAll() {
    for (const controller of this.requests.values()) controller.abort();
    this.requests.clear();
  }
}

/** One key per logical UI operation, retained on failure and reused for retries. */
export class OperationKeys {
  private keys = new Map<string, string>();
  constructor(private createKey = () => crypto.randomUUID()) {}
  get(identity: string, originalKey?: string | null): string {
    let key = this.keys.get(identity);
    if (!key) { key = originalKey ?? this.createKey(); this.keys.set(identity, key); }
    return key;
  }
  complete(identity: string) { this.keys.delete(identity); }
}
