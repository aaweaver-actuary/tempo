import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";

vi.mock("@tempo/board-ui", () => ({ Board: (props: { fen: string; orientation: string }) => <div data-testid="board" data-fen={props.fen} data-orientation={props.orientation} /> }));
vi.mock("../../app/lib/study-connection", () => ({ LEARNING_URL: "", learningFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const initial = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const saved = { games: [{ id: "g1", provider: "lichess", played_at: "2026-01-01", result: "1-0", color: "black" }] };
const authored = { repertoires: [{ id: "r1", name: "Main" }, { id: "r2", name: "Other" }] };

it.each(["/api/product/game-review/games", "/api/repertoires"])("surfaces an initial HTTP failure from %s and retries", async failedPath => {
  let fail = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (fail && url === failedPath) return Response.json({ detail: "temporarily unavailable" }, { status: 503 });
    return Response.json(url === "/api/repertoires" ? authored : saved);
  }));
  render(<GameReviewView />);
  expect((await screen.findByRole("alert")).textContent).toContain("503");
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /lichess/ })).toBeTruthy();
  expect((screen.getByLabelText("Repertoire") as HTMLSelectElement).value).toBe("r1");
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  ["Repertoire", "r2"],
  ["Your color", "black"],
  ["Paste PGN", "1. d4"],
])("ignores a pending comparison after changing %s", async (label, value) => {
  let resolve!: (response: Response) => void;
  const pending = new Promise<Response>(done => { resolve = done; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/repertoires") return Response.json(authored);
    if (url.endsWith("/games")) return Response.json(saved);
    return pending;
  }));
  render(<GameReviewView />);
  await screen.findByRole("button", { name: /lichess/ });
  fireEvent.change(screen.getByLabelText("Paste PGN"), { target: { value: "1. e4" } });
  fireEvent.click(screen.getByRole("button", { name: "Review PGN" }));
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  await act(async () => { resolve(Response.json({ positions: [initial], moves: [], comparison: { kind: "matched" } })); });
  expect(screen.queryByTestId("board")).toBeNull();
  expect(screen.queryByText("matched")).toBeNull();
  expect((screen.getByRole("button", { name: "Review PGN" }) as HTMLButtonElement).disabled).toBe(false);
});

it("does not let an obsolete saved-game failure clear a newer comparison's pending state", async () => {
  let failOld!: (error: Error) => void;
  let resolveNew!: (response: Response) => void;
  const old = new Promise<Response>((_, reject) => { failOld = reject; });
  const current = new Promise<Response>(resolve => { resolveNew = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/repertoires") return Response.json(authored);
    if (url.endsWith("/games")) return Response.json(saved);
    return url.includes("repertoire_id=r1") ? old : current;
  }));
  render(<GameReviewView />);
  const review = await screen.findByRole("button", { name: /lichess/ });
  fireEvent.click(review);
  fireEvent.change(screen.getByLabelText("Repertoire"), { target: { value: "r2" } });
  fireEvent.click(review);
  await act(async () => { failOld(new Error("obsolete failure")); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect((review as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { resolveNew(Response.json({ positions: [initial], moves: [], color: "black", comparison: { kind: "matched" } })); });
  expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("black");
  expect((review as HTMLButtonElement).disabled).toBe(false);
});

it("replays a saved game with the saved player's orientation", async () => {
  const initial = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const next = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/repertoires") return Response.json({ repertoires: [{ id: "r1", name: "Main" }] });
    if (url.endsWith("/games")) return Response.json({ games: [{ id: "g1", provider: "lichess", played_at: "2026-01-01", result: "1-0", color: "black" }] });
    return Response.json({ color: "black", positions: [initial, next], moves: ["e2e4"], comparison: { kind: "departure", ply: 1, played_uci: "e2e4", expected_uci: ["d2d4"] } });
  }));
  render(<GameReviewView />);
  fireEvent.click(await screen.findByRole("button", { name: /lichess/ }));
  await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("black"));
  expect(screen.getByText(/First departure at ply 1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(next);
});
