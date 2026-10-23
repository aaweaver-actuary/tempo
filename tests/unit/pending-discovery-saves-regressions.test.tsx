import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PendingDiscoverySaves } from "../../app/components/pending-discovery-saves";
import {
  DISCOVERY_ADMISSIONS_CHANGED,
  DISCOVERY_ADMISSION_QUEUED,
} from "../../app/lib/discovery-admission-outbox";

const outbox = vi.hoisted(() => ({
  pending: vi.fn(),
  recover: vi.fn(),
  flush: vi.fn(),
  retry: vi.fn(),
  report: vi.fn(),
  enqueue: vi.fn(),
}));
const notices = vi.hoisted(() => ({
  list: vi.fn(),
  resolve: vi.fn(),
}));
const requests = vi.hoisted(() => vi.fn());

vi.mock("../../app/lib/discovery-admission-outbox", () => ({
  DISCOVERY_ADMISSIONS_CHANGED: "tempo-discovery-admissions-changed",
  DISCOVERY_ADMISSION_QUEUED: "tempo-discovery-admission-queued",
  pendingDiscoveryAdmissions: outbox.pending,
  recoverUnacknowledgedDiscoveryAdmissions: outbox.recover,
  flushPendingDiscoveryAdmissions: outbox.flush,
  retryPendingDiscoveryAdmission: outbox.retry,
  reportDiscoveryAdmissionErrors: outbox.report,
  enqueuePendingDiscoveryAdmission: outbox.enqueue,
}));
vi.mock("../../app/lib/notifications", () => ({
  notifications: notices.list,
  resolveNotification: notices.resolve,
}));

function admission(state: "pending" | "accepted" | "failed", opportunityId: string) {
  return {
    opportunityId,
    selectedMoveUci: "g1f3",
    evidenceFingerprint: `${opportunityId}-revision`,
    operationId: `${opportunityId}-operation`,
    state,
    ...(state === "failed" ? { error: "Save needs a retry" } : {}),
  };
}

const savedAdmissions = [
  admission("pending", "pending-id"),
  admission("accepted", "accepted-id"),
  admission("failed", "failed-id"),
];
const saveNotice = {
  id: "save-notice",
  key: "discovery-save:failed-id",
  resolvedAt: null,
};

function dispatch(type: string, detail?: unknown) {
  act(() => window.dispatchEvent(detail === undefined
    ? new Event(type)
    : new CustomEvent(type, { detail })));
}

beforeEach(() => {
  vi.clearAllMocks();
  outbox.pending.mockReturnValue([...savedAdmissions]);
  outbox.recover.mockImplementation(() => {});
  outbox.flush.mockResolvedValue(undefined);
  outbox.retry.mockResolvedValue(undefined);
  outbox.report.mockImplementation(() => {});
  notices.list.mockReturnValue([saveNotice]);
  notices.resolve.mockImplementation(() => {});
  requests.mockResolvedValue(undefined);
  vi.stubGlobal("fetch", requests);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("renders only persisted saves and makes no admission, feed, or proposal request", () => {
  const onQueueChanged = vi.fn().mockResolvedValue(undefined);
  render(<PendingDiscoverySaves onQueueChanged={onQueueChanged} />);

  expect(screen.getByText(/Discovery pending-id/)).toBeTruthy();
  expect(screen.getByText(/Discovery accepted-id/)).toBeTruthy();
  expect(screen.getByText(/Discovery failed-id/)).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Retry save" })).toHaveLength(1);
  expect(outbox.recover).toHaveBeenCalledTimes(1);
  expect(outbox.enqueue).not.toHaveBeenCalled();
  expect(requests).not.toHaveBeenCalled();
  expect(onQueueChanged).not.toHaveBeenCalled();
});

it("retries a failed save by opportunity ID and blocks a retry while busy", async () => {
  let finishRetry!: () => void;
  const retryRequest = new Promise<void>((resolve) => { finishRetry = resolve; });
  outbox.retry.mockReturnValue(retryRequest);
  const view = render(<PendingDiscoverySaves onQueueChanged={requests} interactionBlocked />);
  const blockedButton = screen.getByRole("button", { name: "Retry save" }) as HTMLButtonElement;
  expect(blockedButton.disabled).toBe(true);
  fireEvent.click(blockedButton);
  expect(outbox.retry).not.toHaveBeenCalled();

  view.rerender(<PendingDiscoverySaves onQueueChanged={requests} />);
  const retryButton = screen.getByRole("button", { name: "Retry save" }) as HTMLButtonElement;
  fireEvent.click(retryButton);
  expect(outbox.retry).toHaveBeenCalledWith("failed-id");
  expect(retryButton.disabled).toBe(true);

  await act(async () => { finishRetry(); await retryRequest; });
  expect((screen.getByRole("button", { name: "Retry save" }) as HTMLButtonElement).disabled).toBe(false);
});

it("resolves the save notice and refreshes the queue only on confirmed queue events", async () => {
  const onQueueChanged = vi.fn().mockResolvedValue(undefined);
  render(<PendingDiscoverySaves onQueueChanged={onQueueChanged} />);

  dispatch(DISCOVERY_ADMISSIONS_CHANGED);
  dispatch("storage");
  await act(async () => { await Promise.resolve(); });
  expect(notices.resolve).not.toHaveBeenCalled();
  expect(onQueueChanged).not.toHaveBeenCalled();

  dispatch(DISCOVERY_ADMISSION_QUEUED, { opportunityId: "failed-id" });
  await waitFor(() => expect(notices.resolve).toHaveBeenCalledWith("save-notice", {
    severity: "success",
    message: "Discovery save confirmed.",
  }));
  await waitFor(() => expect(onQueueChanged).toHaveBeenCalledTimes(1));
});

it("shows storage-read errors without dropping records, then clears only that read error", async () => {
  render(<PendingDiscoverySaves onQueueChanged={requests} />);
  outbox.pending.mockImplementationOnce(() => { throw new Error("Saved discovery requests are invalid"); });
  dispatch(DISCOVERY_ADMISSIONS_CHANGED);

  expect(screen.getByText("Saved discovery requests are invalid")).toBeTruthy();
  expect(screen.getByText(/Discovery pending-id/)).toBeTruthy();

  outbox.pending.mockReturnValue([...savedAdmissions]);
  dispatch("storage");
  await waitFor(() => expect(screen.queryByText("Saved discovery requests are invalid")).toBeNull());
  expect(screen.getByText(/Discovery pending-id/)).toBeTruthy();
});

it.each(["flush", "retry", "queue refresh"] as const)(
  "keeps a %s error visible after an unrelated successful storage update",
  async (source) => {
    const failure = `${source} failed`;
    const onQueueChanged = source === "queue refresh"
      ? vi.fn().mockRejectedValue(new Error(failure))
      : vi.fn().mockResolvedValue(undefined);
    if (source === "flush") outbox.flush.mockRejectedValue(new Error(failure));
    if (source === "retry") outbox.retry.mockRejectedValue(new Error(failure));
    render(<PendingDiscoverySaves onQueueChanged={onQueueChanged} />);

    if (source === "retry") fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    if (source === "queue refresh")
      dispatch(DISCOVERY_ADMISSION_QUEUED, { opportunityId: "failed-id" });
    await waitFor(() => expect(screen.getByText(failure)).toBeTruthy());

    dispatch(DISCOVERY_ADMISSIONS_CHANGED);
    expect(screen.getByText(failure)).toBeTruthy();
  },
);

it("cleans up on unmount and remounts one listener and timer set with saved records", async () => {
  vi.useFakeTimers();
  const trackedEvents = [DISCOVERY_ADMISSIONS_CHANGED, DISCOVERY_ADMISSION_QUEUED, "storage"];
  const addListener = vi.spyOn(window, "addEventListener");
  const removeListener = vi.spyOn(window, "removeEventListener");
  const onQueueChanged = vi.fn().mockResolvedValue(undefined);
  const firstView = render(<PendingDiscoverySaves onQueueChanged={onQueueChanged} />);
  expect(outbox.flush).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(1);

  await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
  expect(outbox.flush).toHaveBeenCalledTimes(2);
  firstView.unmount();

  expect(removeListener).toHaveBeenCalledWith(DISCOVERY_ADMISSIONS_CHANGED, expect.any(Function));
  expect(removeListener).toHaveBeenCalledWith(DISCOVERY_ADMISSION_QUEUED, expect.any(Function));
  expect(removeListener).toHaveBeenCalledWith("storage", expect.any(Function));
  expect(vi.getTimerCount()).toBe(0);
  const readsAfterUnmount = outbox.pending.mock.calls.length;
  dispatch(DISCOVERY_ADMISSIONS_CHANGED);
  dispatch("storage");
  dispatch(DISCOVERY_ADMISSION_QUEUED, { opportunityId: "failed-id" });
  expect(outbox.pending).toHaveBeenCalledTimes(readsAfterUnmount);
  expect(onQueueChanged).not.toHaveBeenCalled();

  await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
  expect(outbox.flush).toHaveBeenCalledTimes(2);

  const secondView = render(<PendingDiscoverySaves onQueueChanged={onQueueChanged} />);
  expect(screen.getByText(/Discovery pending-id/)).toBeTruthy();
  expect(screen.getByText(/Discovery accepted-id/)).toBeTruthy();
  expect(screen.getByText(/Discovery failed-id/)).toBeTruthy();
  expect(vi.getTimerCount()).toBe(1);

  const activeListenerCount = (eventName: string) => {
    const active = new Set(addListener.mock.calls
      .filter(([registered]) => registered === eventName)
      .map(([, listener]) => listener));
    removeListener.mock.calls
      .filter(([removed]) => removed === eventName)
      .forEach(([, listener]) => active.delete(listener));
    return active.size;
  };
  for (const eventName of trackedEvents) expect(activeListenerCount(eventName)).toBe(1);

  const readsBeforeUpdate = outbox.pending.mock.calls.length;
  dispatch(DISCOVERY_ADMISSIONS_CHANGED);
  expect(outbox.pending).toHaveBeenCalledTimes(readsBeforeUpdate + 1);
  secondView.unmount();
  expect(vi.getTimerCount()).toBe(0);
  for (const eventName of trackedEvents) expect(activeListenerCount(eventName)).toBe(0);
});
