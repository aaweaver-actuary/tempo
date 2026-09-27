import { render, screen, waitFor } from "@testing-library/react";
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { useStudyPositionIndex } from "../../app/hooks/use-study-position-index";
import { runStudyTask } from "../../app/lib/background-study";
import type { AnalysisLine } from "../../app/types";

it("Builder position index replaces and releases revisions without leaking stale matches", async () => {
  const repertoireId = "position-index-regression";
  const startingFen = new Chess().fen();
  const line = {
    id: "one", repertoireId, repertoireName: "White", title: "One",
    side: "white", startingFen, moves: ["e2e4", "e7e5"],
  } as AnalysisLine;
  function Probe({ lines }: { lines: AnalysisLine[] }) {
    const ready = useStudyPositionIndex(repertoireId, lines);
    return <output data-testid="revision">{ready?.revision ?? "waiting"}</output>;
  }
  const firstLines = [line];
  const view = render(<Probe lines={firstLines} />);
  await waitFor(() => expect(screen.getByTestId("revision").textContent).not.toBe("waiting"));
  const firstRevision = Number(screen.getByTestId("revision").textContent);
  const firstMatches = await runStudyTask<unknown[]>({
    kind: "findPositionMatches", repertoireId, revision: firstRevision, fen: startingFen,
  });
  expect(firstMatches.length).toBeGreaterThan(0);

  view.rerender(<Probe lines={[line]} />);
  await waitFor(() => expect(Number(screen.getByTestId("revision").textContent)).toBeGreaterThan(firstRevision));
  const secondRevision = Number(screen.getByTestId("revision").textContent);
  await expect(runStudyTask({
    kind: "findPositionMatches", repertoireId, revision: firstRevision, fen: startingFen,
  })).rejects.toThrow("unavailable");
  await expect(runStudyTask({
    kind: "findPositionMatches", repertoireId, revision: secondRevision, fen: startingFen,
  })).resolves.toEqual(firstMatches);
});
