import type { Square } from "chess.js";
import type { CardId, DomainDate, FenKey, RepertoireId } from "./shared";

export type ArrowColors = "green" | "red" | "blue" | "yellow";

export type AnnotationArrow = {
  from: Square;
  to: Square;
  color: ArrowColors;
};

export type AnnotationSquare = {
  square: Square;
  color: ArrowColors;
};

export type PositionAnnotation = {
  repertoireId: RepertoireId;
  fenKey: FenKey;
  comment: string;
  arrows: AnnotationArrow[];
  squares: AnnotationSquare[];
  updatedAt: DomainDate;
};

export type TeachingState = {
  cardId: CardId;
  revision: number;
  ply: number;
  taughtAt: DomainDate;
};
