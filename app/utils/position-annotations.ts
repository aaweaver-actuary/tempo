import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { API_URL } from "../const";
import type { PositionAnnotation } from "../types";
import { canonicalFenKey } from "./canonical-line";
import { usesLocalApi } from "./local";

const STORAGE_KEY = "tempo-position-annotations";

function localAnnotations(): PositionAnnotation[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as PositionAnnotation[];
  } catch {
    return [];
  }
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
        const body = (await response.json()) as { annotations: PositionAnnotation[] };
        return body.annotations[0];
      }
    } catch {
      // Browser storage remains a usable fallback if the local service is unavailable.
    }
  }
  return localAnnotations().find(
    (item) => item.repertoireId === repertoireId && item.fenKey === key,
  );
}

export async function savePositionAnnotation(
  annotation: PositionAnnotation,
  fen: string,
): Promise<PositionAnnotation> {
  const normalized = { ...annotation, fenKey: canonicalFenKey(fen), updatedAt: new Date().toISOString() };
  const local = localAnnotations().filter(
    (item) => !(item.repertoireId === normalized.repertoireId && item.fenKey === normalized.fenKey),
  );
  if (normalized.comment || normalized.arrows.length || normalized.squares.length) local.push(normalized);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(local));
  if (usesLocalApi()) {
    const response = await fetch(
      `${API_URL}/api/repertoires/${encodeURIComponent(normalized.repertoireId)}/annotations`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen, comment: normalized.comment, arrows: normalized.arrows, squares: normalized.squares }),
      },
    );
    if (!response.ok) throw new Error("Could not save this position note");
    return (await response.json()) as PositionAnnotation;
  }
  return normalized;
}

export function annotationToShapes(annotation?: PositionAnnotation): DrawShape[] {
  if (!annotation) return [];
  return [
    ...annotation.arrows.map((arrow) => ({ orig: arrow.from as Key, dest: arrow.to as Key, brush: arrow.color })),
    ...annotation.squares.map((square) => ({ orig: square.square as Key, brush: square.color })),
  ];
}

export function shapesToAnnotationParts(shapes: DrawShape[]) {
  return {
    arrows: shapes.flatMap((shape) => shape.dest ? [{ from: shape.orig, to: shape.dest, color: (shape.brush ?? "green") as PositionAnnotation["arrows"][number]["color"] }] : []),
    squares: shapes.flatMap((shape) => !shape.dest ? [{ square: shape.orig, color: (shape.brush ?? "green") as PositionAnnotation["squares"][number]["color"] }] : []),
  };
}
