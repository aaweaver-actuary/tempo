import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { moduleResolution } from "./module-resolution";
import inventory from "./tools/component-build/test-inventory.json";
import frontendConfiguration from "./vitest.config";
const lane = process.env.TEMPO_TEST_LANE as keyof typeof inventory.lanes;
if (!lane || !inventory.lanes[lane]) throw new Error("Choose an explicitly owned test lane");
export default defineConfig({
  resolve: moduleResolution,
  plugins: [react()],
  test: { fakeTimers: frontendConfiguration.test?.fakeTimers, maxWorkers: 2, testTimeout: 15000, environment: "jsdom", include: inventory.lanes[lane], setupFiles: ["tests/unit/setup.ts"] },
});
