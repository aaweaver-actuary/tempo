import { Chess } from "chess.js";
import type { BoardProps, Square } from "@tempo/board-ui";
import { positionsFromMoves } from "../../hooks/use-board-history";
import type { Finding, GameDetail } from "../product-api/contracts";

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
