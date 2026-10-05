import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { localDayKey } from "../../app/utils/local";

describe("disposable PostgreSQL browser calendar", () => {
  it("regular browser and service share the study day across UTC midnight", async () => {
    vi.stubEnv("TZ", "UTC");
    vi.stubEnv("TEMPO_DOCKER_URL", "http://127.0.0.1:3000");
    try {
      const { default: config } = await import("../../playwright.config");
      const serviceTimezones = [...readFileSync("docker-compose.postgres.test.yml", "utf8")
        .matchAll(/^\s+TZ:\s*(\S+)\s*$/gm)].map((match) => match[1]);
      expect(serviceTimezones.length).toBeGreaterThan(0);
      expect(new Set(serviceTimezones)).toEqual(new Set([config.use?.timezoneId]));
      const browserCalendar = new Intl.DateTimeFormat("en-CA", {
        timeZone: config.use?.timezoneId, year: "numeric", month: "2-digit", day: "2-digit",
      });
      expect(browserCalendar.format(new Date("2026-10-03T00:15:00Z"))).toBe("2026-10-02");
      expect(browserCalendar.format(new Date("2026-10-03T04:15:00Z"))).toBe("2026-10-03");
      expect(localDayKey(new Date("2026-10-03T00:15:00Z"))).toBe("2026-10-02");
      expect(localDayKey(new Date("2026-10-03T04:15:00Z"))).toBe("2026-10-03");
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
