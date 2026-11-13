import * as z from "zod";
import type { AnalysisLine } from "../analysis";
import { asSanMove } from "../shared";
import { parseData, validRecords, type ValidationReporter, type ValidationEffects } from "../validation";
import {
  settingsResponseSchema,
  repertoireLinesResponseSchema,
  repertoireLineSchema,
  repertoiresResponseSchema,
  progressResponseSchema,
  tacticProgressSchema,
} from "../schemas";

type ValidationInput = ValidationReporter | ValidationEffects;
function effectsFor(input?: ValidationInput, shouldPublishDiagnostics?: () => boolean): ValidationEffects {
  return { ...(typeof input === "function" ? { reportError: input } : input),
    ...(shouldPublishDiagnostics ? { shouldPublishDiagnostics } : {}) };
}

export function repertoireLinesFromPayload(raw: unknown, input?: ValidationInput): AnalysisLine[] {
  const body = parseData(
    repertoireLinesResponseSchema,
    raw,
    "repertoire lines",
    { effects: effectsFor(input) },
  );
  return validRecords(repertoireLineSchema, body.lines, "repertoire line", effectsFor(input)).map(
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
export function validateWorkspacePayload(url: string, raw: unknown, shouldPublishDiagnostics?: () => boolean, input?: ValidationInput): unknown {
  const parseWorkspaceData = <T>(schema: z.ZodType<T>, value: unknown, source: string) =>
    parseData(schema, value, source, { effects: effectsFor(input, shouldPublishDiagnostics) });
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
          effectsFor(input, shouldPublishDiagnostics),
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
          effectsFor(input, shouldPublishDiagnostics),
        ),
      };
    }
    case "/api/progress":
      return parseWorkspaceData(progressResponseSchema, raw, "study progress");
    case "/api/tactics/progress":
      return parseWorkspaceData(tacticProgressSchema, raw, "tactics progress");
    default:
      return raw;
  }
}
