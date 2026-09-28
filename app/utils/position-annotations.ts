import * as z from "zod";
import { annotationSchema, annotationsResponseSchema } from "../domain/schemas";
import { readStoredValue, validRecords, readJsonResponse } from "../lib/validated-data";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import type { Square } from "chess.js";
import { API_URL } from "../const";
import { asFenKey, asIsoDateString, type PositionAnnotation } from "../types";
import { canonicalFenKey } from "./canonical-line";
import { usesLocalApi } from "./local";
import { confirmOperationResponse, PendingOperationError } from "../lib/operation-status";

const STORAGE_KEY = "tempo-position-annotations";
const PENDING_KEY = "tempo-pending-position-annotation-v1";
type PendingAnnotation = { operationId: string; body: string; repertoireId: string };

function pendingAnnotation(): PendingAnnotation | null {
  const raw = localStorage.getItem(PENDING_KEY);
  if (!raw) return null;
  const parsed: unknown = JSON.parse(raw);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string")
    throw new Error("The pending position note is invalid. Restore browser data before retrying.");
  return parsed as PendingAnnotation;
}

function storeConfirmedAnnotation(annotation: PositionAnnotation): void {
  const remaining = localAnnotations().filter((item) =>
    item.repertoireId !== annotation.repertoireId || item.fenKey !== annotation.fenKey,
  );
  if (annotation.comment || annotation.arrows.length || annotation.squares.length)
    remaining.push(annotation);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(remaining));
}

function localAnnotations(): PositionAnnotation[] {
  if (typeof window === "undefined") return [];
  const raw = readStoredValue(localStorage, STORAGE_KEY, z.array(z.unknown())) ?? [];
  return validRecords(annotationSchema, raw, "saved annotation");
}

export async function loadPositionAnnotation(
  repertoireId: string,
  fen: string,
): Promise<PositionAnnotation | undefined> {
  const key = canonicalFenKey(fen);
  if (usesLocalApi()) {
    try {
      const response = await fetch(
        `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/annotations?fen=${encodeURIComponent(fen)}`,
      );
      if (response.ok) {
        const body = await readJsonResponse(response, annotationsResponseSchema, "position annotations");
        return validRecords(annotationSchema, body.annotations, "position annotation")[0];
      }
    } catch {
      return undefined;
    }
    return undefined;
  }
  return localAnnotations().find(
    (item) => item.repertoireId === repertoireId && item.fenKey === key,
  );
}

export async function savePositionAnnotation(
  annotation: PositionAnnotation,
  fen: string,
): Promise<PositionAnnotation> {
  const normalized = {
    ...annotation,
    fenKey: asFenKey(canonicalFenKey(fen)),
    updatedAt: asIsoDateString(new Date().toISOString()),
  };
  if (usesLocalApi()) {
    const body = JSON.stringify({
      fen, comment: normalized.comment,
      arrows: normalized.arrows, squares: normalized.squares,
    });
    let pending = pendingAnnotation();
    if (pending) {
      const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
      if (!status.ok) throw new PendingOperationError(pending.operationId);
      const receipt = await status.json() as {
        state?: string; response?: unknown; error?: { message?: string };
      };
      if (receipt.state === "pending" && (pending.body !== body ||
          pending.repertoireId !== normalized.repertoireId))
        throw new PendingOperationError(pending.operationId);
      if (receipt.state === "failed") {
        localStorage.removeItem(PENDING_KEY);
        throw new Error(receipt.error?.message ?? "The earlier position note save failed.");
      }
      if (receipt.state === "complete") {
        localStorage.removeItem(PENDING_KEY);
        if (pending.body === body && pending.repertoireId === normalized.repertoireId) {
          const saved = annotationSchema.parse(receipt.response);
          storeConfirmedAnnotation(saved);
          return saved;
        }
        pending = null;
      } else if (receipt.state !== "pending") {
        throw new PendingOperationError(pending.operationId);
      }
    }
    if (!pending) {
      pending = { operationId: crypto.randomUUID(), body,
        repertoireId: normalized.repertoireId };
      localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
    }
    let response = await fetch(
      `${API_URL}/api/repertoires/${encodeURIComponent(normalized.repertoireId)}/annotations`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
        body,
      },
    );
    response = await confirmOperationResponse(response);
    if (!response.ok) throw new Error("Could not save this position note");
    const saved = await readJsonResponse(response, annotationSchema, "saved annotation");
    storeConfirmedAnnotation(saved);
    localStorage.removeItem(PENDING_KEY);
    return saved;
  }
  storeConfirmedAnnotation(normalized);
  return normalized;
}

export function annotationToShapes(
  annotation?: PositionAnnotation,
): DrawShape[] {
  if (!annotation) return [];
  return [
    ...annotation.arrows.map((arrow) => ({
      orig: arrow.from as Key,
      dest: arrow.to as Key,
      brush: arrow.color,
    })),
    ...annotation.squares.map((square) => ({
      orig: square.square as Key,
      brush: square.color,
    })),
  ];
}

export function shapesToAnnotationParts(shapes: DrawShape[]) {
  const squareLike = (value: Key): Square | null =>
    /^[a-h][1-8]$/.test(value) ? (value as Square) : null;

  return {
    arrows: shapes.flatMap((shape) => {
      if (!shape.dest) return [];
      const from = squareLike(shape.orig);
      const to = squareLike(shape.dest);
      if (!from || !to) return [];
      return [
        {
          from,
          to,
          color: (shape.brush ??
            "green") as PositionAnnotation["arrows"][number]["color"],
        },
      ];
    }),
    squares: shapes.flatMap((shape) => {
      if (shape.dest) return [];
      const square = squareLike(shape.orig);
      if (!square) return [];
      return [
        {
          square,
          color: (shape.brush ??
            "green") as PositionAnnotation["squares"][number]["color"],
        },
      ];
    }),
  };
}
