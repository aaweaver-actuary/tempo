import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import RepertoireView from "../../app/views/repertoire_view";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", () => ({
  readWorkspaceResponse: async () => Response.json({ repertoires: [{
    id: "rep", name: "Main", source_name: "main.pgn", line_count: 1,
    card_count: 2, due_count: 0, trained_color: "white",
  }] }),
  invalidateWorkspaceData: vi.fn(),
}));

it("retired opportunity drilldown leaves retained repertoire import and paste actions", async () => {
  const onImport = vi.fn();
  const onPaste = vi.fn();
  render(<RepertoireView imported={[]} onImport={onImport} onPaste={onPaste}
    onRepair={vi.fn()} onDeleteLocal={vi.fn()} onRenameLocal={vi.fn()}
    onQueueChanged={async () => {}} />);

  expect(await screen.findByRole("heading", { name: "Repertoire" })).toBeTruthy();
  expect(screen.getByText("Main")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Opportunities" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Import PGN/ }));
  fireEvent.click(screen.getByRole("button", { name: "Paste analysis" }));
  expect(onImport).toHaveBeenCalledOnce();
  expect(onPaste).toHaveBeenCalledOnce();
});
