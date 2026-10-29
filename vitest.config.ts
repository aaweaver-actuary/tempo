import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { moduleResolution } from "./module-resolution";

export default defineConfig({ resolve: moduleResolution, plugins: [react()], test: { server: { deps: { inline: ["@tempo/board-ui"] } }, // Native IndexedDB tasks progress independently of a page's controlled timers.
  fakeTimers: { toFake: ["Date", "performance", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "cancelIdleCallback"] },
  maxWorkers: process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN ? 1 : 2, testTimeout: 15000, environment: "jsdom", include: ["tests/unit/**/*.test.{ts,tsx}"], setupFiles: ["tests/unit/setup.ts"] } });
