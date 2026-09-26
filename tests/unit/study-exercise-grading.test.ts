import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { evaluateStudyAnswer, studyAnswerSchema, studySpecificationSchema } from "../../app/domain/study-exercises";

type Case = { name: string; fen: string; specification: unknown; answer: unknown; outcome: string };
const cases = JSON.parse(readFileSync("tests/fixtures/study-grading.json", "utf8")) as Case[];

describe("authored study grader golden parity", () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      const specification = studySpecificationSchema.parse(testCase.specification);
      const answer = studyAnswerSchema.parse(testCase.answer);
      expect(evaluateStudyAnswer(specification, answer, testCase.fen).outcome).toBe(testCase.outcome);
    });
  }
});
