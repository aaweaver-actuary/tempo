import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Home from "../../app/views/home_view";
import { useTrainingStore } from "../../app/state/training-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { asCardId, asFenString, asQueueEntryId, asSanMove, type PracticeCard } from "../../app/types";

let retryBury: () => Promise<void>;
vi.mock("../../app/views/training_view", () => ({ default: (props: { onBury: () => Promise<void> }) => {
  retryBury = props.onBury;
  return <div>Training burial fixture</div>;
} }));
vi.mock("../../app/views/fetchAndInitializeQueue", () => ({ fetchAndInitializeQueue: vi.fn() }));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

it("Home burial retry after failed queue refresh replays the selected entry even if the active card changes", async () => {
  localStorage.clear();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  const cards: PracticeCard[] = [42, 43].map((entryId) => ({
    id: asCardId(`burial-${entryId}`), backendId: asCardId(`burial-${entryId}`), queueEntryId: asQueueEntryId(entryId),
    kind: "opening", title: "Burial fixture", subtitle: "", orientation: "white", moves: [asSanMove("e4")], userMoveTarget: 1,
    startingFen: asFenString("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
  }));
  let failRefresh = true;
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async (advance = false) => {
    if (!advance) { useTrainingStore.getState().hydrateLocalQueue(cards, true, 2); return; }
    useTrainingStore.getState().hydrateLocalQueue([cards[1]], true, 1);
    if (failRefresh) {
      failRefresh = false;
      useTrainingStore.getState().setServiceError("Synthetic queue refresh failure");
      throw new Error("Synthetic queue refresh failure");
    }
  });
  const burialRequests: Array<{ url: string; operationId: string }> = [];
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async (input: string, options?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/bury")) {
      burialRequests.push({ url, operationId: (options!.headers as Record<string, string>)["Idempotency-Key"] });
      const queueEntryId = Number(url.split("/").at(-2));
      return Response.json({ buried: true, queue_entry_id: queueEntryId });
    }
    return Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] });
  }));
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(42));
  await act(async () => { await expect(retryBury()).rejects.toThrow("Synthetic queue refresh failure"); });
  const operationId = localStorage.getItem("tempo-bury-operation-42");
  expect(operationId).toBeTruthy();
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(43);
  await act(async () => { await retryBury(); });
  expect(burialRequests).toHaveLength(2);
  expect(burialRequests[1]).toEqual(burialRequests[0]);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  expect(localStorage.getItem("tempo-bury-operation-43")).toBeNull();
});
