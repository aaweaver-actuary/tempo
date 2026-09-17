import { execFileSync } from "node:child_process";
import { expect, it } from "vitest";
import { settingsResponseSchema } from "../../app/domain/schemas";

it("Pydantic settings response and strict Zod adapter accept the same transport fixture", () => {
  const output = execFileSync(
    ".venv/bin/python",
    [
      "-c",
      "from app.models import Settings; print(Settings().model_dump_json())",
    ],
    {
      env: { ...process.env, PYTHONPATH: "backend" },
      encoding: "utf8",
    },
  );
  const raw: unknown = JSON.parse(output);
  expect(settingsResponseSchema.parse(raw)).toEqual(raw);
  expect(
    settingsResponseSchema.safeParse({
      ...settingsResponseSchema.parse(raw),
      new_cards_per_day: -1,
    }).success,
  ).toBe(false);
});
