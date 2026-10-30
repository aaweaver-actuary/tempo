import { defineConfig, type ViteUserConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { moduleResolution } from "./module-resolution";
import { unitWorkerAllocation } from "./scripts/unit-worker-allocation.mjs";

export default defineConfig(async (): Promise<ViteUserConfig> => ({ resolve: moduleResolution, plugins: [react()], test: { // Native IndexedDB tasks progress independently of a page's controlled timers.
  fakeTimers: { toFake: ["Date", "performance", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "cancelIdleCallback"] },
  ...await unitWorkerAllocation(), testTimeout: 15000, environment: "jsdom", include: ["tests/unit/**/*.test.{ts,tsx}"], setupFiles: ["tests/unit/setup.ts"] } }));
