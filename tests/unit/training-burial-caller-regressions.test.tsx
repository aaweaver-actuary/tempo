import { act, cleanup, render, waitFor, screen, fireEvent } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import Home from "../../app/views/home_view";
import { useTrainingStore } from "../../app/state/training-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { asCardId, asFenString, asQueueEntryId, asSanMove, type PracticeCard } from "../../app/types";

let burialPending: boolean | undefined;
vi.mock("../../app/views/training_view", async (importOriginal) => {
  const { default: ActualTrainingView } = await importOriginal<typeof import("../../app/views/training_view")>();
  return { default: (props: React.ComponentProps<typeof ActualTrainingView>) => {
  burialPending = props.burialPending;
  return <ActualTrainingView {...props} />;
} }; });
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
  fireEvent.click(screen.getByRole("button", { name: "Bury" }));
  await screen.findByText(/Could not bury this card.*Synthetic queue refresh failure/);
  const operationId = localStorage.getItem("tempo-bury-operation-42");
  expect(operationId).toBeTruthy();
  expect(burialPending).toBe(true);
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  expect((screen.getByRole("button", { name: "Bury" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Correct" }) as HTMLButtonElement).disabled).toBe(true);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(43);
  fireEvent.click(screen.getByRole("button", { name: "Retry bury" }));
  await waitFor(() => expect(burialPending).toBe(false));
  expect(burialPending).toBe(false);
  expect(burialRequests).toHaveLength(2);
  expect(burialRequests[1]).toEqual(burialRequests[0]);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  expect(localStorage.getItem("tempo-bury-operation-43")).toBeNull();
});


it.each(["transport", "pending", "terminal"])("Home %s burial controls block mutations until a definitive outcome", async (outcome) => {
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  const card: PracticeCard = {
    id: asCardId("burial-42"), backendId: asCardId("burial-42"), queueEntryId: asQueueEntryId(42),
    kind: "opening", title: "Burial fixture", subtitle: "", moves: [asSanMove("e4")], userMoveTarget: 1,
    startingFen: asFenString("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
  };
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    useTrainingStore.getState().hydrateLocalQueue([card], true, 1);
  });
  let rejectedId = "";
  let burialCalls = 0;
  const fetcher = vi.fn(async (input: string, options?: RequestInit) => {
    const url = String(input);
    if (url.includes("/operations/")) return Response.json({ state: "pending" });
    if (url.endsWith("/bury")) {
      burialCalls++;
      const operationId = (options!.headers as Record<string, string>)["Idempotency-Key"];
      if (burialCalls === 1) {
        rejectedId = operationId;
        if (outcome === "transport") throw new Error("Lost response");
        if (outcome === "terminal") return Response.json({ detail: "Stale burial" }, { status: 409 });
        return Response.json({ operation_id: operationId }, { status: 202 });
      }
      expect(operationId === rejectedId).toBe(outcome !== "terminal");
      return Response.json({ buried: true, queue_entry_id: 42 });
    }
    return Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] });
  });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("fetch", fetcher);
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(42));
  fireEvent.click(screen.getByRole("button", { name: "Bury" }));
  await screen.findByRole("button", { name: "Retry bury" });
  const pending = outcome !== "terminal";
  expect(burialPending).toBe(pending);
  for (const name of ["Bury", "Again", "Show move", "Restart", "Correct", "Edit card"]) {
    const button = screen.getByRole("button", { name: new RegExp(name) });
    expect((button as HTMLButtonElement).disabled).toBe(pending);
  }
  expect((screen.getByRole("button", { name: "Retry bury" }) as HTMLButtonElement).disabled).toBe(false);
  expect(useBoardShellStore.getState().board.interactionMode).toBe(pending ? "readonly" : "legal");
  const generation = useTrainingStore.getState().attempt.generation;
  if (pending) {
    const fen = useTrainingStore.getState().currentFenString;
    await act(async () => { useBoardShellStore.getState().board.onMove?.("e2", "e4"); });
    expect(useTrainingStore.getState().currentFenString).toBe(fen);
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
    expect(useTrainingStore.getState().attempt.generation).toBe(generation);
    expect(burialCalls).toBe(1);
  }
  fireEvent.click(screen.getByRole("button", { name: pending ? "Retry bury" : "Bury" }));
  await waitFor(() => expect(burialPending).toBe(false));
  expect(burialCalls).toBe(2);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  await act(async () => { useTrainingStore.getState().setAttemptPhase("opponentReplyPending"); });
  expect((screen.getByRole("button", { name: "Bury" }) as HTMLButtonElement).disabled).toBe(true);
});
