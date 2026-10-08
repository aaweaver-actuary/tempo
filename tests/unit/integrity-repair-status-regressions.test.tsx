import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { IntegrityRepairStatus } from "../../app/components/integrity-repair-status";
import { enqueueIntegrityRepair, pendingIntegrityRepairs } from "../../app/lib/integrity-repair-outbox";
import { browserActivitySnapshot, clearBrowserActivity } from "../../app/lib/browser-activity";
import { notifications } from "../../app/lib/notifications";

afterEach(() => { clearBrowserActivity(); vi.restoreAllMocks(); });

for (const { title, removal } of [
  { title: "explicit stale repair discard does not reconcile or report successful repair", removal: "discard" },
  { title: "replacing a stale repair operation keeps recovery pending without reporting success", removal: "replacement" },
  { title: "external repair operation replacement reconciles evidence without reporting success", removal: "external-replacement" },
  { title: "external stale repair removal requests evidence reconciliation without reporting success", removal: "external" },
]) {
  it(title, async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const repair = enqueueIntegrityRepair({ repertoireId: "rep", issueId: "first", signature: "old", selectedMoveUci: "e2e4" });
    const storageKey = `tempo-pending-integrity-repairs-v3:${repair.operationId}`;
    localStorage.setItem(storageKey, JSON.stringify({ ...repair, phase: "stale", error: "Evidence changed" }));
    const reconcile = vi.fn();
    render(<IntegrityRepairStatus onReconcile={reconcile} onResume={vi.fn()} />);
    expect(screen.getByText(/Needs attention/)).not.toBeNull();
    if (removal === "discard") fireEvent.click(screen.getByRole("button", { name: "Discard obsolete choice" }));
    else if (removal === "replacement") {
      await act(async () => { enqueueIntegrityRepair({ repertoireId: "rep", issueId: "first", signature: "fresh", selectedMoveUci: "d2d4" }); });
      expect(pendingIntegrityRepairs()).toHaveLength(1);
      expect(pendingIntegrityRepairs()[0].operationId).not.toBe(repair.operationId);
      expect(screen.getByText(/d2d4 · Waiting to send/)).not.toBeNull();
    } else {
      const oldValue = localStorage.getItem(storageKey);
      if (removal === "external-replacement") {
        const replacement = { ...repair, operationId: `${repair.operationId}-replacement`, signature: "fresh", selectedMoveUci: "d2d4" };
        localStorage.setItem(`tempo-pending-integrity-repairs-v3:${replacement.operationId}`, JSON.stringify(replacement));
      }
      await act(async () => {
        localStorage.removeItem(storageKey);
        window.dispatchEvent(new StorageEvent("storage", { key: storageKey, oldValue, newValue: null, storageArea: localStorage }));
      });
    }
    expect(reconcile).toHaveBeenCalledTimes(removal.startsWith("external") ? 1 : 0);
    if (removal.startsWith("external")) expect(reconcile).toHaveBeenCalledWith("rep");
    expect(browserActivitySnapshot().find(entry => entry.id === `integrity-repair:${repair.operationId}`))
      .toMatchObject({ title: "Saved repair choice removed", phase: "Removed from this device" });
    expect(browserActivitySnapshot().some(entry => entry.phase === "Validated")).toBe(false);
    expect(notifications().filter(entry => entry.key === `integrity-repair:${repair.operationId}`)
      .some(entry => /confirmed|validated|successfully repaired/i.test(entry.message))).toBe(false);
    if (removal === "external-replacement") {
      expect(pendingIntegrityRepairs()).toHaveLength(1);
      expect(screen.getByText(/d2d4 · Waiting to send/)).not.toBeNull();
    }
    if (removal === "discard") expect(pendingIntegrityRepairs()).toHaveLength(0);
    // An unrelated storage update does not repeat reconciliation for the already removed operation.
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: null, storageArea: localStorage })); });
    expect(reconcile).toHaveBeenCalledTimes(removal.startsWith("external") ? 1 : 0);
  });
}
