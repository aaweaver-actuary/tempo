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

const STORAGE_KEY = "tempo-position-annotations";

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
  const local = localAnnotations().filter(
    (item) =>
      !(
        item.repertoireId === normalized.repertoireId &&
        item.fenKey === normalized.fenKey
      ),
  );
  if (
    normalized.comment ||
    normalized.arrows.length ||
    normalized.squares.length
  )
    local.push(normalized);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(local));
  if (usesLocalApi()) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${encodeURIComponent(normalized.repertoireId)}/annotations`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fen,
          comment: normalized.comment,
          arrows: normalized.arrows,
          squares: normalized.squares,
        }),
      },
    );
    if (!response.ok) throw new Error("Could not save this position note");
    return readJsonResponse(response, annotationSchema, "saved annotation");
  }
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
