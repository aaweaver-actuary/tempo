// @vitest-environment node
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { computeStudyTask } from "../../app/lib/study-computation";
import { trainingBuilderSession, resolveTrainingRoute, prependTrainingRoute } from "../../app/lib/training-builder-route";
import { builderSessionSchema, studyTaskSchema } from "../../app/domain/schemas";
import { canonicalizeLine } from "../../app/utils/canonical-line";
import { asCardId, asFenString, asLineId, asRepertoireId, asSanMove } from "../../app/types";
const standardFen = new Chess().fen();
const savedMoves = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
function savedLine(moves = savedMoves, id = "authored", repertoireId = "najdorf", startingFen = standardFen) {
  return canonicalizeLine({ id: asLineId(id), repertoireId: asRepertoireId(repertoireId), repertoireName: "Najdorf", title: id,
    side: "black", startingFen: asFenString(startingFen), moves: moves.map(asSanMove) });
}
const original = savedLine();
const board = new Chess();
for (const move of savedMoves.slice(0, 8)) board.move(move);
const partialFen = board.fen();
const partialCard = { id: asCardId("card"), kind: "opening" as const, title: "Najdorf", subtitle: "", startingFen: asFenString(partialFen),
  moves: ["Bg2", "e5"].map(asSanMove), repertoireId: asRepertoireId("najdorf"), orientation: "black" as const, userMoveTarget: 1 };

it("partial training card restores the exact original prefix and keeps the full card continuation", () => {
  const session = trainingBuilderSession(partialCard, { fen: partialFen, cursor: 0 }, true);
  const candidates = resolveTrainingRoute([original], "najdorf", session.startingFen, session.history.map(move => move.uci));
  expect(candidates).toHaveLength(1);
  expect(candidates[0].prefix).toHaveLength(8);
  const restored = prependTrainingRoute(candidates[0], session.history);
  expect(restored.map(move => move.uci)).toEqual(original.moves);
  expect(restored[7].fen).toBe(partialFen);
});

it("training route restoration excludes similar positions wrong repertoires and incompatible continuations", () => {
  const wrongTail = savedLine([...savedMoves.slice(0, 8), "d3", "e5"], "wrong-tail");
  const otherRepertoire = savedLine(savedMoves, "other", "other-repertoire");
  const similarPosition = savedLine([...savedMoves.slice(0, 7), "Bg4", "Bg2", "e5"], "similar");
  expect(resolveTrainingRoute([wrongTail, otherRepertoire, similarPosition], "najdorf", partialFen, ["f1g2", "e7e5"])).toEqual([]);
});

it("ambiguous transposed training routes remain distinct but duplicate earlier routes collapse", () => {
  const first = savedLine(["Nf3", "Nf6", "g3", "g6", "Bg2", "Bg7"], "first");
  const second = savedLine(["g3", "g6", "Nf3", "Nf6", "Bg2", "Bg7"], "second");
  const duplicate = savedLine(["Nf3", "Nf6", "g3", "g6", "Bg2", "Bg7", "O-O"], "duplicate");
  const transposition = new Chess();
  for (const move of ["Nf3", "Nf6", "g3", "g6"]) transposition.move(move);
  const candidates = resolveTrainingRoute([first, duplicate, second], "najdorf", transposition.fen(), ["f1g2", "f8g7"]);
  expect(candidates.map(candidate => candidate.prefix.map(move => move.san))).toEqual([["Nf3", "Nf6", "g3", "g6"], ["g3", "g6", "Nf3", "Nf6"]]);
});

it("custom-FEN training routes retain their authored starting position instead of assuming the standard opening", () => {
  const customStart = new Chess(); customStart.move("e4"); customStart.move("c5");
  const customLine = savedLine(savedMoves.slice(2), "custom", "najdorf", customStart.fen());
  const candidates = resolveTrainingRoute([customLine], "najdorf", partialFen, ["f1g2", "e7e5"]);
  expect(candidates[0].startingFen).toBe(customStart.fen());
  expect(candidates[0].prefix).toHaveLength(6);
});

it("training route recovery handles repeated positions by comparing the continuation at each occurrence", () => {
  const repetition = savedLine(["Nf3", "Nf6", "Ng1", "Ng8", "e4", "e5"], "repetition");
  const candidates = resolveTrainingRoute([repetition], "najdorf", standardFen, ["e2e4", "e7e5"]);
  expect(candidates).toHaveLength(1);
  expect(candidates[0].prefix).toHaveLength(4);
});

it("invalid training cursors and off-route displayed positions report an actionable handoff error", () => {
  expect(() => trainingBuilderSession(partialCard, { fen: standardFen, cursor: 0 }, true)).toThrow("viewed position");
  expect(() => trainingBuilderSession(partialCard, { fen: partialFen, cursor: 3 }, true)).toThrow("viewed position");
});

it("training route context is optional in legacy Builder sessions and roundtrips unresolved sessions", () => {
  const pending = trainingBuilderSession(partialCard, { fen: partialFen, cursor: 0 }, true);
  expect(builderSessionSchema.parse(pending).trainingRouteToResolve?.repertoireId).toBe("najdorf");
  const legacy = { ...pending }; delete legacy.trainingRouteToResolve;
  expect(builderSessionSchema.parse(legacy).version).toBe(1);
  expect(builderSessionSchema.safeParse({ ...pending, trainingRouteToResolve: { ...pending.trainingRouteToResolve, cardRevision: 0 } }).success).toBe(false);
});

it("training route recovery runs through the validated regular study-worker protocol", () => {
  const task = studyTaskSchema.parse({ kind: "resolveTrainingRoute", repertoireId: "najdorf", startingFen: partialFen, moves: ["f1g2", "e7e5"], lines: [original] });
  expect(computeStudyTask(task as Parameters<typeof computeStudyTask>[0])).toEqual(resolveTrainingRoute([original], "najdorf", partialFen, ["f1g2", "e7e5"]));
});
