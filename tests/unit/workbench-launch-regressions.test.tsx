import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkbenchLaunch } from "../../app/components/workbench-launch";
import { workbenchUrl, workbenchUrlForBuild } from "../../app/lib/workbench-navigation";
import { usesLocalApi } from "../../app/utils/local";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: vi.fn(() => true) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.mocked(usesLocalApi).mockReturnValue(true); });
const configured = "http://127.0.0.1:8150/?run=example-run&category=repertoire_miss&dismissal=open";

it("connected Workbench launcher is explicit, separate and side-effect free", () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("__TEMPO_WORKBENCH_URL__", configured);
  render(<WorkbenchLaunch />);
  const link = screen.getByRole("link", { name: "Open Workbench" });
  expect(link.getAttribute("href")).toBe(configured);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  fireEvent.pointerEnter(link); fireEvent.focus(link);
  expect(fetch).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(0);
  expect(link.title).toContain("Connect once in that tab");
});

it("connected Workbench launcher is absent when unconfigured or outside the local app", () => {
  vi.stubGlobal("__TEMPO_WORKBENCH_URL__", "");
  const view = render(<WorkbenchLaunch />);
  expect(screen.queryByRole("link")).toBeNull();
  vi.stubGlobal("__TEMPO_WORKBENCH_URL__", configured);
  vi.mocked(usesLocalApi).mockReturnValue(false);
  view.rerender(<WorkbenchLaunch />);
  expect(screen.queryByRole("link")).toBeNull();
});

it("connected Workbench build and runtime reject unsafe public URL settings without echoing them", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,unsafe", "https://user:synthetic-password@example.test/",
    "https://example.test/?token=synthetic-secret", "https://example.test/?unexpected=synthetic-secret",
    "https://example.test/#synthetic-secret", "/relative"] ) {
    expect(workbenchUrl(value)).toBeNull();
    try { workbenchUrlForBuild(value); throw new Error("Expected build rejection"); }
    catch (error) {
      expect(String(error)).toContain("TEMPO_WORKBENCH_URL must be a public");
      expect(String(error)).not.toContain(value);
      expect(String(error)).not.toContain("synthetic-secret");
      expect(String(error)).not.toContain("synthetic-password");
    }
  }
});

it("connected Workbench build preserves only the supported public navigation fields", () => {
  expect(workbenchUrlForBuild(undefined)).toBe("");
  expect(workbenchUrlForBuild("")).toBe("");
  expect(workbenchUrlForBuild(configured)).toBe(configured);
  expect(workbenchUrlForBuild("https://analysis.example.test/")).toBe("https://analysis.example.test/");
});


it("connected full-app build boundary rejects unsafe settings before emitting a define", async () => {
  const { loadConfigFromFile } = await import("vite");
  try {
    for (const value of ["javascript:alert(1)", "https://user:synthetic-secret@example.test/", "https://example.test/?unknown=synthetic-secret"]) {
      vi.stubEnv("TEMPO_WORKBENCH_URL", value);
      try {
        await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
        throw new Error("Expected build rejection");
      } catch (error) {
        expect(String(error)).toContain("TEMPO_WORKBENCH_URL must be a public");
        expect(String(error)).not.toContain(value);
        expect(String(error)).not.toContain("synthetic-secret");
      }
    }
    vi.stubEnv("TEMPO_WORKBENCH_URL", configured);
    const loaded = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
    expect(loaded?.config.define?.__TEMPO_WORKBENCH_URL__).toBe(JSON.stringify(configured));
  } finally { vi.unstubAllEnvs(); }
});


it("connected Workbench Docker build accepts an optional public launcher setting before compilation", async () => {
  const { readFile } = await import("node:fs/promises");
  const dockerfile = await readFile("Dockerfile.web", "utf8");
  const lines = dockerfile.split("\n").map(line => line.trim()).filter(Boolean);
  const build = lines.indexOf("RUN npm run build:local");
  expect(build).toBeGreaterThan(0);
  expect(lines[build - 1]).toBe('ARG TEMPO_WORKBENCH_URL=""');
});
