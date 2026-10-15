import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["tests/controlled-browser/unit/*.test.ts"], environment: "node", maxWorkers: 1, testTimeout: 15000 } });
