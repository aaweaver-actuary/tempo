import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Navbar, { WORKSPACES } from "../../app/components/Navbar";
import { useTaskTabs } from "../../app/components/task-tabs";
import { prepareLegacyWorkspaceNavigation } from "../../app/views/legacy-workspace";

function TabsProbe({ onChange }: { onChange?: (name: string) => void }) {
  const tools = useTaskTabs(["Solve", "Packs"], "Solve", "ui-focus-tabs", onChange);
  return <section {...tools.panelProps}>{tools.tabs}</section>;
}

describe("UI focus regressions", () => {
  it("primary navigation omits retired dashboards and their aliases fall back to Train", () => {
    render(<Navbar view="train" setView={() => undefined} />);
    expect(WORKSPACES.map((workspace) => workspace.label)).toEqual([
      "Train",
      "Tactics",
      "Endgames",
      "Repertoire",
      "Builder",
      "Studies",
      "Games",
      "Settings",
    ]);
    expect(screen.queryByRole("button", { name: "Insights" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Progress" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Statistics" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Compare positions" })).toBeNull();
    for (const alias of ["compare", "insights", "progress", "statistics"] as const)
      expect(prepareLegacyWorkspaceNavigation(alias)).toBe("train");
  });

  it("context tabs preserve workspace state across resize and navigation", () => {
    sessionStorage.removeItem("ui-focus-tabs");
    const onChange = vi.fn();
    const { rerender } = render(<TabsProbe onChange={onChange} />);
    const packs = screen.getByRole("tab", { name: "Packs" });
    fireEvent.click(packs);
    expect(screen.getByRole("tab", { name: "Packs" }).getAttribute("aria-selected")).toBe("true");
    expect(onChange).toHaveBeenCalledWith("Packs");
    rerender(<TabsProbe onChange={onChange} />);
    expect(screen.getByRole("tab", { name: "Packs" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tablist").parentElement?.getAttribute("data-active-task")).toBe("Packs");
  });

  it("all tactical difficulties remain directly accessible", () => {
    sessionStorage.removeItem("ui-focus-tabs");
    render(<TabsProbe />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Solve" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "Packs" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Packs" }).getAttribute("aria-controls")).toBeTruthy();
  });
});
