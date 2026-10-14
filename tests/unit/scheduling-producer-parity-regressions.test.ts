// @vitest-environment node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { resolvePython } from "../../scripts/resolve-python.mjs";

it("M02 installed Python scheduling producer retains the fixed-time serialized golden contract", () => {
  const producer = `
import json
from dataclasses import asdict
from datetime import datetime, timezone
from fsrs import Card
from tempo_scheduling import schedule_review
now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
results = []
for outcome in ('again', 'correct'):
    result = asdict(schedule_review(outcome, fsrs_card_json=Card(card_id=1, due=now).to_json(), reviewed_at=now, review_day=now.date()))
    result['due_date'] = result['due_date'].isoformat()
    results.append(result)
print(json.dumps(results))
`;
  const output = execFileSync(resolvePython(), ["-I", "-c", producer], { encoding: "utf8" });
  expect(JSON.parse(output)).toEqual(JSON.parse(readFileSync("packages/scheduling/tests/golden.json", "utf8")));
});
