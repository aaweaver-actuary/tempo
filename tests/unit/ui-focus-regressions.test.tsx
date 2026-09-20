import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Navbar, { WORKSPACES } from "../../app/components/Navbar";
import { useTaskTabs } from "../../app/components/task-tabs";

function TabsProbe({ onChange }: { onChange?: (name: string) => void }) {
  const tools = useTaskTabs(["Solve", "Packs"], "Solve", "ui-focus-tabs", onChange);
  return <section {...tools.panelProps}>{tools.tabs}</section>;
}

describe("UI focus regressions", () => {
  it("primary navigation exposes one Insights destination", () => {
    render(<Navbar view="train" setView={() => undefined} />);
    expect(WORKSPACES.map((workspace) => workspace.label)).toEqual([
      "Train",
      "Tactics",
      "Endgames",
      "Repertoire",
      "Builder",
      "Games",
      "Insights",
      "Settings",
    ]);
    expect(screen.getAllByRole("button", { name: "Insights" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Progress" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Statistics" })).toBeNull();
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
