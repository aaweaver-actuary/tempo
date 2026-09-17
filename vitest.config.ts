import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { moduleResolution } from "./module-resolution";

export default defineConfig({ resolve: moduleResolution, plugins: [react()], test: { maxWorkers: 2, testTimeout: 15000, environment: "jsdom", include: ["tests/unit/**/*.test.{ts,tsx}"], setupFiles: ["tests/unit/setup.ts"] } });
