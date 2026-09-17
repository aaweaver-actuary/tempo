import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { resetWorkspaceCache } from "../../app/lib/workspace-data";
import { useTrainingStore } from "../../app/state/training-store";
import { clearDataDiagnostics } from "../../app/lib/validated-data";
beforeEach(() => {
  resetWorkspaceCache();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  clearDataDiagnostics();
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, "scrollTo", { value: vi.fn(), configurable: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
