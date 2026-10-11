import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RepertoireView from "../../app/views/repertoire_view";
import { beginPrefixTransition } from "../../app/lib/prefix-transition-command";
import { prefixTransitionApplySchema, prefixTransitionPlanSchema, transitionSummary } from "../../app/domain/prefix-transition";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import transition from "../fixtures/prefix-comparison/transition.json";
import structural from "../fixtures/prefix-comparison/structural.json";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: (input: string) => fetch(input),
}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it("issue81_offline_repertoire_load_does_not_hide_saved_recovery", async () => {
  const operation = await beginPrefixTransition("rep", prefixTransitionApplySchema.parse(transition.request),
    transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black"));
  const fetcher = vi.fn(async () => { throw new TypeError("Local service unavailable"); });
  vi.stubGlobal("fetch", fetcher);
  render(<RepertoireView imported={[]} onImport={vi.fn()} onBrowse={vi.fn()} onResolveGap={vi.fn()}
    onShowGamesAtPosition={vi.fn()} onRepair={vi.fn()} onDeleteLocal={vi.fn()} onRenameLocal={vi.fn()}
    onQueueChanged={vi.fn()} onTrain={vi.fn()} />);
  const resume = await screen.findByRole("button", { name: "Resume prefix transition" });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(resume);
  const dialog = await screen.findByRole("dialog");
  await screen.findByText("Selected source routes: 2", { exact: false });
  expect(dialog.textContent).toContain("Selected source routes: 2");
  expect(dialog.textContent).toContain(operation.operationId);
  await screen.findByRole("heading", { name: "Delivery unconfirmed" });
  expect(fetcher.mock.calls.every(([, options]: unknown[]) => !(options as RequestInit | undefined)?.method)).toBe(true);
});

beforeEach(() => { installPrefixTransitionTestLocks(); });
