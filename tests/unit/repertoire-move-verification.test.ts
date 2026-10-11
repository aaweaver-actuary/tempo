import { afterEach, expect, it, vi } from "vitest";
import { verifySavedRepertoireMove } from "../../app/lib/repertoire-move-verification";

afterEach(() => vi.unstubAllGlobals());

it("saved_move_verification_continues_only_current_advancing_source_pages", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({ verified: null, next_cursor: "line-a", source_token: "current" }))
    .mockResolvedValueOnce(Response.json({ verified: false, next_cursor: null, source_token: "current" }));
  vi.stubGlobal("fetch", fetchMock);
  expect(await verifySavedRepertoireMove("saved", "fen", "c2c4", new AbortController().signal)).toBe(false);
  expect(fetchMock.mock.calls[1][0]).toContain("cursor=line-a&source_token=current");
});

it.each([
  { verified: false, next_cursor: null, source_token: "changed" },
  { verified: null, next_cursor: "line-a", source_token: "current" },
  { verified: "false", next_cursor: null, source_token: "current" },
])("saved_move_verification_rejects_changed_nonadvancing_and_invalid_evidence (%j)", async result => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ verified: null, next_cursor: "line-a", source_token: "current" }))
    .mockResolvedValueOnce(Response.json(result)));
  await expect(verifySavedRepertoireMove("saved", "fen", "c2c4", new AbortController().signal)).rejects.toThrow();
});
