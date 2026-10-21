import { readTrainingBurials, writeTrainingBurials, removeTrainingBurials } from "../../database/browser/adapters/practice/training-burials";
import { z } from "zod";
import type { PracticeCard } from "../types";
import { localDayKey } from "../utils/local";

const burialSchema = z.object({ day: z.string(), cardIds: z.array(z.string()) });

function readBurials() {
  const stored = readTrainingBurials();
  return stored ? burialSchema.parse(JSON.parse(stored)) : null;
}

export function restoreBrowserTrainingBurials(
  queue: number[], cards: PracticeCard[], day = localDayKey(),
): number[] {
  const burials = readBurials();
  if (!burials) return queue;
  if (burials.day === day) {
    const buriedIds = new Set(burials.cardIds);
    return queue.filter((cardIndex) => !buriedIds.has(cards[cardIndex]?.id));
  }
  // Tomorrow's normal reset owns admission; burial only excludes today.
  removeTrainingBurials();
  return queue;
}

export function rememberBrowserTrainingBurial(cardId: string, day = localDayKey()): void {
  const previousBurials = readBurials();
  const cardIds = new Set(previousBurials?.day === day ? previousBurials.cardIds : []);
  cardIds.add(cardId);
  writeTrainingBurials(day, cardIds);
}
