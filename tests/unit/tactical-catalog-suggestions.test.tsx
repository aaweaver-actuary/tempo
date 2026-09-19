import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TacticalCatalogPanel } from "../../app/components/tactical-catalog";

describe("TacticalCatalogPanel suggestions", () => {
  it("activates a suggested tactics pack only after user action", () => {
    const onStartSuggested = vi.fn();
    render(
      <TacticalCatalogPanel
        catalog={{ version: 1, groups: [], themes: [], packs: [] }}
        progress={{}}
        selectedPackId=""
        onSelect={() => undefined}
        onActivate={() => undefined}
        busy={false}
        recommendations={[{
          motif: "fork",
          miss_count: 3,
          opportunity_count: 5,
          miss_rate: 0.6,
          total_loss_cp: 500,
          supporting_games: [],
          window_days: 30,
          recommended_pack_id: "fork-easy-01",
          recommended_pack_active: false,
        }]}
        onStartSuggested={onStartSuggested}
      />,
    );
    expect(onStartSuggested).not.toHaveBeenCalled();
    expect(screen.getByText("You missed 3 of 5 fork opportunities in the past 30 days.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start this motif" }));
    expect(onStartSuggested).toHaveBeenCalledTimes(1);
  });
});
