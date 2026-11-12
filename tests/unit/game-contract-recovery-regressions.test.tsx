import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { DataDiagnosticsNotice } from "../../app/components/data-diagnostics-notice";
import { validateWorkspacePayload } from "../../app/domain/adapters/workspace-adapters";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  reportDataDiagnostic,
} from "../../app/lib/validated-data";
import {
  readWorkspaceData,
  resetWorkspaceCache,
} from "../../app/lib/workspace-data";

// Retained cache/diagnostic obligations now exercise the supported repertoire owner.
const repertoireUrl = "http://127.0.0.1:8000/api/repertoires";
const repertoire = { id: "repertoire-1", name: "Italian", source_name: "import.pgn",
  line_count: 1, card_count: 2, due_count: 1 };
const envelope = (repertoires: typeof repertoire[]) => ({ repertoires });

beforeEach(() => {
  localStorage.clear();
  clearDataDiagnostics();
  resetWorkspaceCache();
});

it("supported repertoire responses retain validated fields", () => {
  expect(validateWorkspacePayload(repertoireUrl, envelope([repertoire]))).toEqual(envelope([repertoire]));
});

it("repertoire envelope drift fails once instead of silently emptying the library", () => {
  expect(() => validateWorkspacePayload(repertoireUrl, { repertoires: [], private_field: true }))
    .toThrow(/Invalid repertoires data/);
  expect(dataDiagnostics()).toHaveLength(1);
  expect(dataDiagnostics()[0].message).toContain("private_field");
});

it("invalid repertoire responses are never cached as empty success", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ repertoires: null })));
  await expect(readWorkspaceData(repertoireUrl)).rejects.toThrow(/Invalid repertoires data/);
  expect(Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
    .filter(key => key?.includes("api/repertoires"))).toEqual([]);
});

it("corrected live repertoire data wins over an older persisted cache", async () => {
  localStorage.setItem(`tempo-workspace-cache-v2:${repertoireUrl}`, JSON.stringify({
    version: 2, savedAt: Date.now(), data: envelope([{ ...repertoire, id: "stale" }]),
  }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(envelope([repertoire]))));
  const immediate = await readWorkspaceData(repertoireUrl) as { repertoires: Array<{ id: string }> };
  expect(immediate.repertoires[0].id).toBe(repertoire.id);
});

it("repeated diagnostics are grouped and clear after successful validation", async () => {
  for (const recordId of ["one", "two", "three"])
    reportDataDiagnostic("repertoire", { id: recordId }, 'record: Unrecognized key: "private_field"', recordId);
  render(<DataDiagnosticsNotice />);
  expect(screen.getByText(/1 data issue type affecting 3 records/)).toBeTruthy();
  expect(screen.getByText(/examples one, two, three/)).toBeTruthy();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(envelope([repertoire]))));
  await readWorkspaceData(repertoireUrl);
  await waitFor(() => expect(dataDiagnostics()).toHaveLength(0));
});
