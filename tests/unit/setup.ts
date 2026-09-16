import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { resetWorkspaceCache } from "../../app/lib/workspace-data";
beforeEach(() => {
  resetWorkspaceCache();
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, "scrollTo", { value: vi.fn(), configurable: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
