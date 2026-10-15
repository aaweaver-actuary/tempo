import { defineConfig } from "vitest/config";
import { discoverControlledUnitFiles } from "./test-unit.mjs";
export default defineConfig({ test: { include: discoverControlledUnitFiles(), environment: "node", maxWorkers: 1, testTimeout: 15000 } });
