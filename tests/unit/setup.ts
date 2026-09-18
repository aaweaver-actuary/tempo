import { afterEach, beforeEach, vi } from "vitest";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { cleanup, configure } from "@testing-library/react";
import { resetWorkspaceCache } from "../../app/lib/workspace-data";
import { useTrainingStore } from "../../app/state/training-store";
import { clearDataDiagnostics } from "../../app/lib/validated-data";
configure({ asyncUtilTimeout: 5000 });
beforeEach(() => {
  resetWorkspaceCache();
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true);
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  clearDataDiagnostics();
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, "scrollTo", { value: vi.fn(), configurable: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
// jsdom has no layout engine; real sizing and observation are covered in browsers.
if (typeof ResizeObserver === 'undefined') {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
}
