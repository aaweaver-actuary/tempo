import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../fixtures/game-review-current-api.json";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";
import { createGameReviewApi } from "../../app/features/game-review/api";
import { GameDetailSchema } from "../../app/features/product-api/contracts";

afterEach(cleanup);

it("renders the current API encounter as pending first introduction without a write or false availability", async () => {
  const parsed = GameDetailSchema.parse(fixture.detail);
  expect(parsed.receipts).toEqual(fixture.detail.receipts);
  expect(Object.isFrozen(parsed.receipts[0])).toBe(true);
  const fetchFixture = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    expect(options?.method).toBe("GET");
    const path = String(input);
    if (path === "/api/product/game-review/games") return new Response(JSON.stringify(fixture.recent));
    if (path === fixture.detail_path) return new Response(JSON.stringify(fixture.detail));
    throw new Error(`Unexpected fixture request: ${path}`);
  });
  const api = createGameReviewApi({ fetch: fetchFixture, endpoints: {
    recentGames: "/api/product/game-review/games",
    gameDetail: () => fixture.detail_path,
    sync: "/forbidden-sync",
    retryFeedback: "/forbidden-feedback",
  } });
  render(<GameReviewView api={api} />);
  const label = new RegExp(`Lichess · ${fixture.detail.game_id}`);
  fireEvent.click(await screen.findByRole("button", { name: label }));
  expect(await screen.findByText(/Disposition: first introduction · Admission: pending/)).toBeTruthy();
  expect(screen.queryByText(/available for Study/)).toBeNull();
  expect(screen.getByRole("group", { name: /Original game board/ })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(fetchFixture).toHaveBeenCalledTimes(2);
});
