import { afterEach, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import type { Page } from "@playwright/test";
import { connectedReviewState, nativeStudyRecords } from "../../browser/native-study-storage";

afterEach(() => vi.unstubAllGlobals());

it("F2 native inspection before lazy application startup never creates an empty Study database", async () => {
  vi.stubGlobal("indexedDB", new IDBFactory());
  const page = { evaluate: (callback: (argument?: string) => unknown, argument?: string) => callback(argument) } as unknown as Page;
  expect(await connectedReviewState(page, "original")).toBe("missing");
  expect(await nativeStudyRecords(page)).toEqual([]);
  expect(await indexedDB.databases()).toEqual([]);
});
