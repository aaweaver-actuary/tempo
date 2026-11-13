// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { evaluateStudyAnswer, studyAnswerSchema, studySpecificationSchema, validateStudyExercise } from "../../app/domain/study-exercises";

type Case = { name: string; fen: string; specification: unknown; answer: unknown; expected_validation_stage: string; expected_semantic_result: unknown };
const cases = JSON.parse(readFileSync("tests/fixtures/study-grading.json", "utf8")) as Case[];
describe("authored study grader shared v1 parity", () => {
  for (const testCase of cases) it(testCase.name, () => {
    const specification = studySpecificationSchema.safeParse(testCase.specification);
    if (!specification.success) { expect(testCase.expected_validation_stage).toBe("specification_schema"); return; }
    if (!validateStudyExercise(specification.data, testCase.fen)) { expect(testCase.expected_validation_stage).toBe("specification_semantic"); return; }
    const answer = studyAnswerSchema.safeParse(testCase.answer);
    if (!answer.success) { expect(testCase.expected_validation_stage).toBe("answer_schema"); return; }
    expect(testCase.expected_validation_stage).toBe("graded");
    expect(evaluateStudyAnswer(specification.data, answer.data, testCase.fen)).toEqual(testCase.expected_semantic_result);
  });
});
