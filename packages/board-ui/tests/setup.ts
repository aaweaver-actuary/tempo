import { vi } from "vitest";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
if (typeof HTMLElement !== "undefined") vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
  x: 0, y: 0, left: 0, top: 0, right: 643, bottom: 643, width: 643, height: 643, toJSON() {},
});
