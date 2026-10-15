// @vitest-environment node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { canonicalFenKey } from "@tempo/chess-line";

it("M02 installed Python scheduling producer retains the fixed-time serialized golden contract", () => {
  const producer = `
import json
from dataclasses import asdict
from datetime import datetime, timezone
from tempo_scheduling import schedule_review
now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
results = []
for outcome in ('again', 'correct'):
    initial = json.dumps(dict(card_id=1, state=1, step=0, stability=None, difficulty=None,
                              due=now.isoformat(), last_review=None))
    result = asdict(schedule_review(outcome, fsrs_card_json=initial, reviewed_at=now, review_day=now.date()))
    result['due_date'] = result['due_date'].isoformat()
    results.append(result)
print(json.dumps(results))
`;
  const output = execFileSync(resolvePython(), ["-I", "-c", producer], { encoding: "utf8" });
  expect(JSON.parse(output)).toEqual(JSON.parse(readFileSync("packages/scheduling/tests/golden.json", "utf8")));
});

it("M04 installed identity producer preserves shared digests and equivalent TypeScript FEN keys", () => {
  const fixtures = JSON.parse(readFileSync("tests/fixtures/card-id-parity.json", "utf8")) as Array<{
    fen: string; moves: string[]; cardId: string;
  }>;
  const producer = `
import json, sys
from tempo_chess_core import canonical_fen_key, card_id
print(json.dumps([dict(fenKey=canonical_fen_key(case['fen']), cardId=card_id(case['fen'], case['moves']))
                  for case in json.load(sys.stdin)]))
`;
  const output = execFileSync(resolvePython(), ["-I", "-c", producer], {
    encoding: "utf8", input: JSON.stringify(fixtures),
  });
  expect(JSON.parse(output)).toEqual(fixtures.map(({ fen, cardId }) => ({
    fenKey: canonicalFenKey(fen), cardId,
  })));
});
