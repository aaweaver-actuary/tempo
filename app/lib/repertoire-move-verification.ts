import { API_URL } from "../const";

/** Verify only the submitted position; never build a browser repertoire index. */
export async function verifySavedRepertoireMove(repertoireId: string, fen: string, moveUci: string, signal: AbortSignal): Promise<boolean> {
  let cursor = "";
  let sourceToken: string | undefined;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const parameters = new URLSearchParams({ fen, move_uci: moveUci, cursor });
    if (sourceToken) parameters.set("source_token", sourceToken);
    const response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/move-membership?${parameters}`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(5_000)]),
    });
    if (!response.ok) throw new Error("Saved repertoire evidence is unavailable or changed. Reload and retry the move.");
    const result: unknown = await response.json();
    if (!result || typeof result !== "object" || !("verified" in result) || !("source_token" in result) || !("next_cursor" in result)
      || typeof result.source_token !== "string" || !result.source_token
      || (sourceToken !== undefined && sourceToken !== result.source_token)) {
      throw new Error("Saved repertoire verification returned invalid evidence. Reload and retry.");
    }
    sourceToken = result.source_token;
    if (typeof result.verified === "boolean" && result.next_cursor === null) return result.verified;
    if (result.verified !== null || typeof result.next_cursor !== "string" || result.next_cursor <= cursor) {
      throw new Error("Saved repertoire verification could not advance. Reload and retry.");
    }
    cursor = result.next_cursor;
  }
  throw new Error("Saved repertoire verification took too long. Retry the move or inspect this repertoire.");
}
