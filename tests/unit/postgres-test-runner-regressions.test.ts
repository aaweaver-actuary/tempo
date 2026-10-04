// @vitest-environment node
import { expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { buildPostgresPlaywrightArguments, parsePostgresTestOptions } from
  "../../scripts/postgres-test-options.mjs";
import { createIsolatedTestEnvironment } from "../../scripts/test-environment.mjs";

const cleanEnvironment = { NODE_ENV: "test" as const };

it("PostgreSQL browser and disposable API share the same day across the UTC midnight boundary", async () => {
  vi.stubEnv("TEMPO_DOCKER_URL", "http://127.0.0.1:48124");
  vi.stubEnv("TZ", "UTC");
  try {
    const { default: browserConfiguration } = await import("../../playwright.config");
    const databaseTimezones = [...readFileSync("docker-compose.postgres.test.yml", "utf8")
      .matchAll(/TZ: (\S+)/g)].map((match) => match[1]);
    expect(databaseTimezones.length).toBeGreaterThan(0);
    expect(new Set(databaseTimezones)).toEqual(new Set(["America/New_York"]));
    expect(browserConfiguration.use?.timezoneId).toBe(databaseTimezones[0]);
    expect(process.env.TZ).toBe(databaseTimezones[0]);
    for (const project of browserConfiguration.projects ?? [])
      expect(project.use?.timezoneId ?? browserConfiguration.use?.timezoneId).toBe(databaseTimezones[0]);
    const afterUtcMidnight = new Date("2026-10-04T00:41:54Z");
    expect([afterUtcMidnight.getFullYear(), afterUtcMidnight.getMonth(), afterUtcMidnight.getDate()])
      .toEqual([2026, 9, 3]);
    const calendarDay = (timeZone: string) => new Intl.DateTimeFormat("en-CA", {
      timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    }).format(afterUtcMidnight);
    expect(calendarDay(browserConfiguration.use!.timezoneId!)).toBe("2026-10-03");
    expect(calendarDay("UTC")).toBe("2026-10-04");
  } finally {
    vi.unstubAllEnvs();
  }
});

it("PostgreSQL runner selects a single unfiltered browser matrix by default", () => {
  const options = parsePostgresTestOptions([], cleanEnvironment);
  expect(options).toMatchObject({ skipBrowser: false, browserFile: null, browserGrep: null });
  expect(buildPostgresPlaywrightArguments(options)).toEqual(["playwright", "test"]);
});

it("PostgreSQL runner forwards a focused spec as discrete arguments", () => {
  const options = parsePostgresTestOptions(["--browser-file", "accessibility.spec.ts"], cleanEnvironment, process.cwd());
  expect(buildPostgresPlaywrightArguments(options)).toEqual([
    "playwright", "test", "tests/browser/accessibility.spec.ts",
  ]);
  const grep = parsePostgresTestOptions(["--browser-grep", "Builder [review]"], cleanEnvironment);
  expect(buildPostgresPlaywrightArguments(grep)).toEqual([
    "playwright", "test", "--grep", "Builder [review]",
  ]);
});

it("PostgreSQL runner rejects empty, unknown, conflicting, and inherited filters", () => {
  expect(() => parsePostgresTestOptions(["--browser-grep", "  "], cleanEnvironment)).toThrow("non-empty value");
  expect(() => parsePostgresTestOptions(["--unknown"], cleanEnvironment)).toThrow("Unknown PostgreSQL");
  expect(() => parsePostgresTestOptions(["--browser-file", "builder.spec.ts", "--browser-grep", "Builder"], cleanEnvironment))
    .toThrow("Choose one browser focus");
  expect(() => parsePostgresTestOptions(["--skip-browser", "--browser-grep", "Builder"], cleanEnvironment))
    .toThrow("cannot be combined");
  expect(() => parsePostgresTestOptions([], { ...cleanEnvironment, TEMPO_PG_BROWSER_GREP: "product" })).toThrow("clear inherited");
});

it("PostgreSQL runner propagates failures and scopes destructive cleanup to its unique project", () => {
  const runner = readFileSync("scripts/test-postgres-docker.mjs", "utf8");
  expect(runner).toContain("const project = `tempo-pg-regressions-${process.pid}-${randomBytes(4).toString(\"hex\")}`");
  expect(runner).toContain("let resourcesCreated = false");
  expect(runner).toContain("if (resourcesCreated)");
  expect(runner).toContain('[...compose, "down", "--rmi", "local", "-v"]');
  expect(runner).toContain("process.exit(failed ? 1 : 0)");
});

it("test environment strips dangerous product database and broker settings before any runner work", () => {
  const environment = createIsolatedTestEnvironment({
    DATABASE_URL: "postgresql://product",
    TEMPO_DATABASE_WRITE_URL: "postgresql://product-writer",
    TEMPO_REDIS_URL: "redis://product",
    TEMPO_DB_PATH: "/Users/andy/tempo/data/tempo.db",
    PGHOST: "product-db",
    TEMPO_PG_TEST_PORT: "5432",
    TEMPO_TEST_DOCKER_URL: "http://product:8000",
    PATH: "/usr/bin",
    NODE_ENV: "test" as const,
  }, { TEMPO_PG_TEST_PORT: "48123", TEMPO_DOCKER_URL: "http://127.0.0.1:48124" });
  expect(environment).toMatchObject({ PATH: "/usr/bin", TEMPO_PG_TEST_PORT: "48123",
    TEMPO_DOCKER_URL: "http://127.0.0.1:48124" });
  for (const unsafeKey of ["DATABASE_URL", "TEMPO_DATABASE_WRITE_URL", "TEMPO_REDIS_URL",
    "TEMPO_DB_PATH", "PGHOST", "TEMPO_TEST_DOCKER_URL"])
    expect(environment).not.toHaveProperty(unsafeKey);
});
