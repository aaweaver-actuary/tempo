import { defineConfig } from "vitest/config";

export default defineConfig({ test: {
  environment: "jsdom", include: ["tests/*.test.ts", "tests/*.test.tsx"],
  setupFiles: ["tests/setup.ts"], maxWorkers: 1, fileParallelism: false,
  testTimeout: 5000,
} });
