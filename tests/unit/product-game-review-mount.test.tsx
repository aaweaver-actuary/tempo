import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../../app/lib/study-connection", () => ({ independentStudy: false }));
vi.mock("../../app/lib/workspace-data", () => ({ preloadView: vi.fn(async () => undefined) }));
import Navbar from "../../app/components/Navbar";
import { canOpenWorkspace } from "../../app/lib/study-workspace-policy";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("opens Game Review in the legacy workspace and retains the independent Study boundary", () => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  const setView = vi.fn();
  render(<Navbar view="train" setView={setView} />);
  fireEvent.click(screen.getByRole("button", { name: "Game Review" }));
  expect(setView).toHaveBeenCalledWith("games");
  expect(canOpenWorkspace("games", false)).toBe(true);
  expect(canOpenWorkspace("games", true)).toBe(false);
});
