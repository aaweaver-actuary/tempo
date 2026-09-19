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
  endgameTemplatesSchema,
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
export function validateWorkspacePayload(url: string, raw: unknown): unknown {
  const path = new URL(url, "http://tempo.local").pathname;
  switch (path) {
    case "/api/settings":
      return parseData(settingsResponseSchema, raw, "settings");
    case "/api/repertoire/lines": {
      const body = parseData(
        repertoireLinesResponseSchema,
        raw,
        "repertoire lines",
      );
      return {
        lines: validRecords(
          repertoireLineSchema,
          body.lines,
          "repertoire line",
        ),
      };
    }
    case "/api/repertoires": {
      const body = parseData(
        z.strictObject({ repertoires: z.array(z.unknown()) }),
        raw,
        "repertoires",
      );
      return {
        repertoires: validRecords(
          repertoiresResponseSchema.shape.repertoires.element,
          body.repertoires,
          "repertoire",
        ),
      };
    }
    case "/api/progress":
      return parseData(progressResponseSchema, raw, "study progress");
    case "/api/tactics/progress":
      return parseData(tacticProgressSchema, raw, "tactics progress");
    case "/api/games/summary": {
      return parseData(gamesSummarySchema, raw, "games");
    }
    case "/api/endgames/templates":
      return parseData(endgameTemplatesSchema, raw, "endgame templates");
    default:
      return raw;
  }
}
