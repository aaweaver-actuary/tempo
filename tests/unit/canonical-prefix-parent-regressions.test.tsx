import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import RepertoireView from "../../app/views/repertoire_view";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: (input: string) => fetch(input),
}));
afterEach(() => { vi.unstubAllGlobals(); localStorage.removeItem("tempo-pending-repertoire-delete-v1"); });

it("retained repertoire menu excludes retired prefix producer and preserves an unreadable deletion journal", async () => {
  const journal = "unreadable";
  localStorage.setItem("tempo-pending-repertoire-delete-v1", journal);
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _options?: RequestInit) => Response.json({ repertoires: [{ id: "rep", name: "Italian",
    source_name: "italian.pgn", line_count: 1, card_count: 1, due_count: 0 }] }));
  vi.stubGlobal("fetch", fetchMock);
  const onQueueChanged = vi.fn();
  render(<RepertoireView imported={[]} onImport={vi.fn()} onRepair={vi.fn()} onDeleteLocal={vi.fn()}
    onRenameLocal={vi.fn()} onQueueChanged={onQueueChanged} />);
  await screen.findByText("Italian");
  expect(screen.getByRole("alert").textContent).toContain("Pending repertoire deletion unavailable");
  fireEvent.click(screen.getByText("•••"));
  const menu = screen.getByRole("menu");
  expect(within(menu).getAllByRole("menuitem").map(item => item.textContent)).toEqual(["Rename", "⇩ Export PGN", "Delete"]);
  expect(within(menu).queryByRole("menuitem", { name: /canonical prefix/i })).toBeNull();
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBe(journal);
  expect(onQueueChanged).not.toHaveBeenCalled();
  expect(fetchMock.mock.calls.some(([input]) => /prefix/i.test(String(input)))).toBe(false);
  expect(fetchMock.mock.calls.every(([, options]) => !options || !(options as RequestInit).method || (options as RequestInit).method === "GET")).toBe(true);
});
