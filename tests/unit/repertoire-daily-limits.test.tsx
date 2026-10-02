import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RepertoireDailyLimits } from "../../app/components/repertoire-daily-limits";
import { pendingRepertoireLimit, saveRepertoireLimit } from "../../app/lib/repertoire-settings-save";
import { readWorkspaceResponse } from "../../app/lib/workspace-data";
import { PendingOperationError } from "../../app/lib/operation-status";

vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: vi.fn(), invalidateWorkspaceData: vi.fn(),
}));
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
const result = (limit: number | null, id = "rep") => ({ repertoire_id: id, new_cards_per_day: limit, effective_new_cards_per_day: limit ?? 10 });
const repertoire = { id: "rep", name: "French", source_name: "synthetic", line_count: 1, card_count: 20, due_count: 10, new_cards_per_day: null, effective_new_cards_per_day: 10 };
function loadRepertoires() { vi.mocked(readWorkspaceResponse).mockResolvedValue(Response.json({ repertoires: [repertoire] })); }

it("repertoire limits save a custom zero, report the effective limit, and reset to the default", async () => {
  loadRepertoires();
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => Response.json(result(JSON.parse(init.body as string).new_cards_per_day)));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<RepertoireDailyLimits defaultLimit={10} enabled local />);
  const allowance = await screen.findByLabelText("French allowance");
  expect(screen.getByText(/Unused allowance does not carry over/)).toBeTruthy();
  fireEvent.change(allowance, { target: { value: "custom" } });
  const input = screen.getByLabelText("French new cards per day");
  fireEvent.change(input, { target: { value: "" } });
  expect((screen.getByRole("button", { name: "Save limit for French" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(input, { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Save limit for French" }));
  await screen.findByText("Current limit: 0/day · New cards paused");
  expect(fetcher.mock.calls[0][1].body).toBe('{"new_cards_per_day":0}');
  fireEvent.change(allowance, { target: { value: "default" } });
  fireEvent.click(screen.getByRole("button", { name: "Save limit for French" }));
  await screen.findByText("Current limit: 10/day");
  view.rerender(<RepertoireDailyLimits defaultLimit={12} enabled local />);
  expect(screen.getByText("Current limit: 12/day")).toBeTruthy();
  expect(screen.queryByLabelText("French new cards per day")).toBeNull();
});

it("repertoire pending saves retain exact bytes and identity through lost transport and reload", async () => {
  let savedBody = "", savedKey = "";
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    savedBody = init.body as string; savedKey = (init.headers as Record<string, string>)["Idempotency-Key"];
    throw new Error("Lost transport");
  }));
  await expect(saveRepertoireLimit("rep", 5)).rejects.toThrow("Lost transport");
  expect(pendingRepertoireLimit("rep")).toEqual({ new_cards_per_day: 5 });
  loadRepertoires();
  render(<RepertoireDailyLimits defaultLimit={10} enabled local />);
  const input = await screen.findByLabelText("French new cards per day") as HTMLInputElement;
  expect(input.value).toBe("5"); expect(input.disabled).toBe(true);
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Response.json({ state: "pending" });
    expect(init?.method).toBe("PUT");
    return Response.json(result(5));
  });
  vi.stubGlobal("fetch", fetcher);
  fireEvent.click(screen.getByRole("button", { name: "Check pending save for French" }));
  await screen.findByText("Current limit: 5/day");
  expect(fetcher.mock.calls[1][1]?.body).toBe(savedBody);
  expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({ "Idempotency-Key": savedKey });
  expect(pendingRepertoireLimit("rep")).toBeNull();
});

it("repertoire pending responses lock edits and a terminal failure allows correction", async () => {
  loadRepertoires();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => url.includes("/operations/")
    ? Response.json({ state: "pending" })
    : Response.json({ operation_id: (init?.headers as Record<string, string>)["Idempotency-Key"] }, { status: 202 })));
  await expect(saveRepertoireLimit("rep", 5)).rejects.toBeInstanceOf(PendingOperationError);
  render(<RepertoireDailyLimits defaultLimit={10} enabled local />);
  expect((await screen.findByLabelText("French allowance")).hasAttribute("disabled")).toBe(true);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "failed", error: { message: "Repertoire was removed" } })));
  fireEvent.click(screen.getByRole("button", { name: "Check pending save for French" }));
  await screen.findByText("Repertoire was removed");
  await waitFor(() => expect(screen.getByLabelText("French allowance").hasAttribute("disabled")).toBe(false));
  expect(pendingRepertoireLimit("rep")).toBeNull();
});

it("repertoire completed receipts validate identity and finish without another write", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("Lost"); }));
  await expect(saveRepertoireLimit("rep", 5)).rejects.toThrow();
  const fetcher = vi.fn(async () => Response.json({ state: "complete", response: result(5, "wrong") }));
  vi.stubGlobal("fetch", fetcher);
  await expect(saveRepertoireLimit("rep", 10)).rejects.toThrow("different repertoire settings");
  expect(pendingRepertoireLimit("rep")).not.toBeNull();
  fetcher.mockImplementation(async () => Response.json({ state: "complete", response: result(5) }));
  expect(await saveRepertoireLimit("rep", 10)).toEqual(result(5));
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(pendingRepertoireLimit("rep")).toBeNull();
});

it("repertoire load errors offer retry and demo mode explains local availability", async () => {
  vi.mocked(readWorkspaceResponse).mockRejectedValueOnce(new Error("Database unavailable"));
  loadRepertoires();
  const view = render(<RepertoireDailyLimits defaultLimit={10} enabled local />);
  await screen.findByText(/Repertoire limits unavailable: Database unavailable/);
  fireEvent.click(screen.getByRole("button", { name: "Retry repertoire limits" }));
  await screen.findByLabelText("French allowance");
  view.rerender(<RepertoireDailyLimits defaultLimit={10} enabled local={false} />);
  expect(screen.getByText("Repertoire overrides require local Tempo.")).toBeTruthy();
  expect(screen.queryByLabelText("French allowance")).toBeNull();
});
