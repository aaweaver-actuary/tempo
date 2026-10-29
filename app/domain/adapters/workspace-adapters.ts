import * as z from "zod";
import type { AnalysisLine } from "../analysis";
import { asSanMove } from "../shared";
import { parseData, validRecords } from "../../lib/validated-data";
import {
  settingsResponseSchema,
  repertoireLinesResponseSchema,
  repertoireLineSchema,
  repertoiresResponseSchema,
  progressResponseSchema,
  gamesSummarySchema,
  tacticProgressSchema,
} from "../schemas";

export function repertoireLinesFromPayload(raw: unknown): AnalysisLine[] {
  const body = parseData(
    repertoireLinesResponseSchema,
    raw,
    "repertoire lines",
  );
  return validRecords(repertoireLineSchema, body.lines, "repertoire line").map(
    (line) => ({
      id: line.id,
      repertoireId: line.repertoire_id,
      repertoireName: line.repertoire_name,
      title: line.name,
      side: line.trained_color,
      startingFen: line.start_fen,
      moves: line.moves.map(asSanMove),
    }),
  );
}

// The cache holds transport values, never unchecked domain values.
export function validateWorkspacePayload(url: string, raw: unknown, shouldPublishDiagnostics?: () => boolean): unknown {
  const parseWorkspaceData = <T>(schema: z.ZodType<T>, value: unknown, source: string) =>
    parseData(schema, value, source, undefined, undefined, shouldPublishDiagnostics);
  const path = new URL(url, "http://tempo.local").pathname;
  switch (path) {
    case "/api/settings":
      return parseWorkspaceData(settingsResponseSchema, raw, "settings");
    case "/api/repertoire/lines": {
      const body = parseWorkspaceData(
        repertoireLinesResponseSchema,
        raw,
        "repertoire lines",
      );
      return {
        lines: validRecords(
          repertoireLineSchema,
          body.lines,
          "repertoire line",
          shouldPublishDiagnostics,
        ),
      };
    }
    case "/api/repertoires": {
      const body = parseWorkspaceData(
        z.strictObject({ repertoires: z.array(z.unknown()) }),
        raw,
        "repertoires",
      );
      return {
        repertoires: validRecords(
          repertoiresResponseSchema.shape.repertoires.element,
          body.repertoires,
          "repertoire",
          shouldPublishDiagnostics,
        ),
      };
    }
    case "/api/progress":
      return parseWorkspaceData(progressResponseSchema, raw, "study progress");
    case "/api/tactics/progress":
      return parseWorkspaceData(tacticProgressSchema, raw, "tactics progress");
    case "/api/games/summary": {
      return parseWorkspaceData(gamesSummarySchema, raw, "games");
    }
    default:
      return raw;
  }
}
