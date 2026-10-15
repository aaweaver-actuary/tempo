import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { StudyStorageBoundary } from "../../app/components/study-storage-boundary";
import { closeStudyIntentStorage, initializeStudyIntentStorage, studyIntentStore, studyIntentStorageReady } from "../../app/lib/study-intent-storage";
const command = (key: string) => ({ key, kind: "study-command", attemptId: key, requestId: key, endpoint: "/attempts", createdAt: "2026-10-15T16:00:00Z", payload: {} });

it.each([false, true])("pr285_storage_retry_actual_button_rehydrates_or_stays_blocked (retry denied: %s)", async denied => {
  const store = await studyIntentStore(); await store.put(command("retained"));
  render(<StudyStorageBoundary><p>Study mounted</p></StudyStorageBoundary>);
  await screen.findByText("Study mounted");
  const refreshFault = vi.spyOn(store, "list").mockRejectedValue(new Error("IndexedDB refresh failed"));
  await act(async () => { await store.put(command("wake")); });
  await screen.findByText(/IndexedDB refresh failed/);
  expect(screen.queryByText("Study mounted")).toBeNull();
  refreshFault.mockRestore();
  const openFault = denied ? vi.spyOn(indexedDB, "open").mockImplementation(() => { throw new DOMException("storage denied", "SecurityError"); }) : undefined;
  fireEvent.click(screen.getByRole("button", { name: "Retry study storage" }));
  if (denied) {
    await screen.findByText(/storage denied/); expect(studyIntentStorageReady()).toBe(false);
    expect(screen.queryByText("Study mounted")).toBeNull();
  } else {
    await screen.findByText("Study mounted"); expect(studyIntentStorageReady()).toBe(true);
    const reopened = await studyIntentStore(); expect(reopened).not.toBe(store); expect(await reopened.get("retained")).toBeDefined();
  }
  openFault?.mockRestore();
});
it("pr285_storage_retry_initial_denial_blocks_then_recovers", async () => {
  closeStudyIntentStorage();
  const fault = vi.spyOn(indexedDB, "open").mockImplementation(() => { throw new DOMException("initial denied", "SecurityError"); });
  render(<StudyStorageBoundary><p>Study mounted</p></StudyStorageBoundary>);
  await screen.findByText(/initial denied/); expect(screen.queryByText("Study mounted")).toBeNull();
  fault.mockRestore(); fireEvent.click(screen.getByRole("button", { name: "Retry study storage" }));
  await screen.findByText("Study mounted");
});
function holdNextOpening() {
  let release!: () => void, entered!: () => void;
  const held = new Promise<void>(resolve => { entered = resolve; });
  const open = indexedDB.open.bind(indexedDB);
  const spy = vi.spyOn(indexedDB, "open").mockImplementationOnce((...args) => {
    const request = open(...args);
    request.addEventListener("success", event => {
      event.stopImmediatePropagation();
      release = () => request.onsuccess?.call(request, event);
      entered();
    }, { once: true });
    return request;
  });
  return { held, release: () => release(), restore: () => spy.mockRestore() };
}
it("pr285_storage_retry_superseded_opening_cannot_replace_healthy_store", async () => {
  closeStudyIntentStorage(); const barrier = holdNextOpening();
  const earlier = initializeStudyIntentStorage(); const rejected = expect(earlier).rejects.toThrow("superseded");
  await barrier.held; closeStudyIntentStorage();
  const recovered = await initializeStudyIntentStorage();
  barrier.release(); await rejected;
  expect(await studyIntentStore()).toBe(recovered); expect(studyIntentStorageReady()).toBe(true); barrier.restore();
});
it("pr285_storage_retry_unmounted_boundary_ignores_delayed_opening", async () => {
  closeStudyIntentStorage(); const barrier = holdNextOpening();
  const mounted = render(<StudyStorageBoundary><p>Study mounted</p></StudyStorageBoundary>);
  await barrier.held; mounted.unmount();
  await act(async () => { barrier.release(); await initializeStudyIntentStorage(); });
  expect(screen.queryByText("Study mounted")).toBeNull(); barrier.restore();
});
it("pr285_storage_retry_version_change_requires_reload_and_keeps_study_blocked", async () => {
  render(<StudyStorageBoundary><p>Study mounted</p></StudyStorageBoundary>); await screen.findByText("Study mounted");
  const upgraded = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("tempo-study-intents", 2); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  try {
    await waitFor(() => expect(screen.queryByText("Study mounted")).toBeNull());
    expect(screen.getByRole("button", { name: "Reload Tempo" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry study storage" }));
    await screen.findByText(/Could not open durable study storage/); expect(studyIntentStorageReady()).toBe(false);
  } finally { upgraded.close(); }
});

it("pr285_storage_retry_healthy_initialization_preserves_the_mounted_study_generation", async () => {
  const { useEffect } = await import("react"); const mounted = vi.fn(), unmounted = vi.fn();
  const storage = await import("../../app/lib/study-intent-storage"); const store = await studyIntentStore();
  let release!: () => void; const barrier = new Promise<void>(resolve => { release = resolve; });
  const checking = barrier.then(() => store); vi.spyOn(storage, "initializeStudyIntentStorage").mockReturnValueOnce(checking);
  function Study() { useEffect(() => { mounted(); return unmounted; }, []); return <p>Existing study generation</p>; }
  render(<StudyStorageBoundary><Study /></StudyStorageBoundary>);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText("Existing study generation")).toBeTruthy();
  await act(async () => { release(); await checking; });
  expect(mounted).toHaveBeenCalledOnce(); expect(unmounted).not.toHaveBeenCalled();
});
