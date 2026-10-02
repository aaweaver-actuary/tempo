import { z } from "zod";
import type { PracticeCard } from "../types";
import { localDayKey } from "../utils/local";

const STORAGE_KEY = "tempo-training-burials";
const burialSchema = z.object({ day: z.string(), cardIds: z.array(z.string()) });

function readBurials() {
  const stored = localStorage.getItem(STORAGE_KEY);
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
  const restoredQueue = [...queue];
  for (const cardId of burials.cardIds) {
    const cardIndex = cards.findIndex((card) => card.id === cardId);
    if (cardIndex >= 0 && !restoredQueue.includes(cardIndex)) restoredQueue.push(cardIndex);
  }
  // Save the restored queue before clearing its durable return markers.
  localStorage.setItem("tempo-daily-queue", JSON.stringify(restoredQueue));
  localStorage.removeItem(STORAGE_KEY);
  return restoredQueue;
}

export function rememberBrowserTrainingBurial(cardId: string, day = localDayKey()): void {
  const previousBurials = readBurials();
  const cardIds = new Set(previousBurials?.day === day ? previousBurials.cardIds : []);
  cardIds.add(cardId);
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ day, cardIds: [...cardIds] }));
}
