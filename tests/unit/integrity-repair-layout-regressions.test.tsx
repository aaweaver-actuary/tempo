import { act, fireEvent, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { IntegrityRepairStatus } from "../../app/components/integrity-repair-status";
import { INTEGRITY_REPAIRS_CHANGED } from "../../app/lib/integrity-repair-outbox";

const saved = vi.hoisted(() => ({ repairs: [{ operationId: "repair", repertoireId: "rep", phase: "validating" }] }));
vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/integrity-repair-outbox", () => ({
  INTEGRITY_REPAIRS_CHANGED: "repairs-changed", INTEGRITY_REPAIR_CONFIRMED: "repair-confirmed",
  pendingIntegrityRepairs: () => saved.repairs, flushIntegrityRepairs: async () => undefined,
  retryIntegrityRepair: vi.fn(), discardStaleIntegrityRepair: vi.fn(),
}));

it("repair confirmation removes its message but reserves board layout until the held drop is processed", () => {
  saved.repairs = [{ operationId: "repair", repertoireId: "rep", phase: "validating" }];
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.push(callback); return frames.length; });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  const view = render(<><IntegrityRepairStatus onConfirmed={vi.fn()} onResume={vi.fn()} />
    <div className="board-viewport" dangerouslySetInnerHTML={{ __html: '<piece class="dragging"></piece>' }} /></>);
  const status = view.container.querySelector<HTMLElement>("[data-integrity-repair-status]") ?? view.container;
  vi.spyOn(status, "getBoundingClientRect").mockReturnValue({ height: 78 } as DOMRect);
  act(() => { saved.repairs = []; window.dispatchEvent(new Event(INTEGRITY_REPAIRS_CHANGED)); });
  expect(view.queryByText("Repair validating")).toBeNull();
  expect(status.style.minHeight).toBe("78px");
  fireEvent.mouseUp(document);
  // Chessground handles the same mouseup before the next animation frame.
  expect(status.style.minHeight).toBe("78px");
  act(() => { frames.forEach(callback => callback(0)); });
  expect(status.style.minHeight).toBe("");
  view.unmount();
});
