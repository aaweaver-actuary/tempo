import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Button } from "../components/buttons/BaseButton";
import { createGameReviewApi } from "../features/game-review/api";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "../lib/operation-status";
import { API_URL } from "../const";
import { readPendingProductGameSync, storePendingProductGameSync, clearPendingProductGameSync } from "../../database/browser/adapters/games-analysis/game-sync";

const configurationSchema = z.object({
  workspace_id: z.string(), provider: z.enum(["lichess", "chesscom"]), account: z.string().min(1),
  window: z.object({ start_at: z.string(), end_at: z.string(), precision: z.string() }),
  repertoire_ids: z.array(z.string()).min(1), speeds: z.array(z.string()).min(1),
  rated: z.boolean().nullable(), interval_seconds: z.number().nullable(), revision: z.number().int(),
});
type Configuration = z.infer<typeof configurationSchema>;
type Provider = Configuration["provider"];
const speeds = ["bullet", "blitz", "rapid", "classical"] as const;
const gameReviewBase = `${API_URL}/api/product/game-review`;
const client = createGameReviewApi({ fetch: (...argumentsList) => globalThis.fetch(...argumentsList), endpoints: {
  recentGames: `${gameReviewBase}/games`,
  gameDetail: ({ provider, game_id, repertoire_id }) => `${gameReviewBase}/games/${provider}/${encodeURIComponent(game_id)}${repertoire_id ? `?repertoire_id=${encodeURIComponent(repertoire_id)}` : ""}`,
  sync: `${gameReviewBase}/sync`, retryFeedback: `${gameReviewBase}/feedback/retry`,
} });
type PendingSave = { operationId: string; body: string };
const pendingMutationSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("save"), operationId: z.string().min(1).max(128), body: z.string() }).strict(),
  z.object({ kind: z.literal("run"), provider: z.enum(["lichess", "chesscom"]), operation_id: z.string().min(1).max(128) }).strict(),
]);

function recoverMutation(scope: string, workspaceId: string) {
  try {
    const raw = typeof window === "undefined" ? null : readPendingProductGameSync(scope);
    const pending = raw === null ? null : pendingMutationSchema.parse(JSON.parse(raw));
    let provider: Provider = pending?.kind === "run" ? pending.provider : "lichess";
    if (pending?.kind === "save") {
      const body = z.object({ expected_revision: z.number().int().nonnegative(), configuration: configurationSchema }).parse(JSON.parse(pending.body));
      if (body.configuration.workspace_id !== workspaceId) throw new Error("workspace mismatch");
      provider = body.configuration.provider;
    }
    return { pending, provider, error: "" };
  } catch {
    return { pending: null, provider: "lichess" as Provider,
      error: "The original Games request could not be read. Restore browser data before starting another request." };
  }
}

export function GameSyncSettings({ workspaceId, repertoires }: {
  workspaceId: string; repertoires: readonly { id: string; name: string }[];
}) {
  const scope = `${gameReviewBase}:${workspaceId}`;
  const [recovery] = useState(() => recoverMutation(scope, workspaceId));
  const [provider, setProvider] = useState<Provider>(recovery.provider);
  const [loaded, setLoaded] = useState<Configuration | null>(null);
  const [account, setAccount] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selectedSpeeds, setSpeeds] = useState<string[]>([]);
  const [selectedRepertoires, setRepertoires] = useState<string[]>([]);
  const [rated, setRated] = useState("");
  const [interval, setInterval] = useState("");
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [generation, setGeneration] = useState(0);
  const pendingSave = useRef<PendingSave | null>(recovery.pending?.kind === "save" ? recovery.pending : null);
  const pendingRun = useRef<{ provider: Provider; operation_id: string } | null>(recovery.pending?.kind === "run"
    ? { provider: recovery.pending.provider, operation_id: recovery.pending.operation_id } : null);
  const [unconfirmed, setUnconfirmed] = useState(recovery.pending?.kind === "save");
  const [runPending, setRunPending] = useState(recovery.pending?.kind === "run");

  function clearMutation(operationId: string) {
    const raw = readPendingProductGameSync(scope);
    const current = raw === null ? null : pendingMutationSchema.parse(JSON.parse(raw));
    if (current && (current.kind === "save" ? current.operationId : current.operation_id) === operationId)
      clearPendingProductGameSync(scope);
  }

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(recovery.error); setMessage("");
    setLoaded(null); setAccount(""); setStart(""); setEnd("");
    setSpeeds([]); setRepertoires([]); setRated(""); setInterval("");
    void (async () => {
      try {
        const response = await fetch(`${gameReviewBase}/configuration/${provider}`, {
          credentials: "same-origin", signal: controller.signal,
        });
        if (!response.ok) throw new Error("Could not load game sync settings.");
        const body: unknown = await response.json();
        const value = body === null ? null : configurationSchema.parse(body);
        if (controller.signal.aborted) return;
        setLoaded(value); setAccount(value?.account ?? "");
        setStart(value?.window.start_at ? new Date(value.window.start_at).toISOString().slice(0, 16) : "");
        setEnd(value?.window.end_at ? new Date(value.window.end_at).toISOString().slice(0, 16) : "");
        setSpeeds(value?.speeds ?? []); setRepertoires(value?.repertoire_ids ?? []);
        setRated(value ? value.rated === null ? "both" : value.rated ? "rated" : "casual" : "");
        setInterval(value?.interval_seconds === null || !value ? "" : String(value.interval_seconds));
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not load settings.");
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    })();
    return () => controller.abort();
  }, [provider, generation, recovery.error]);

  async function save() {
    setMessage(""); setError("");
    const startTime = Date.parse(start + "Z"), endTime = Date.parse(end + "Z");
    if (!pendingSave.current && (!account.trim() || !Number.isFinite(startTime) || !Number.isFinite(endTime) || startTime >= endTime || !rated ||
        !selectedSpeeds.length || !selectedRepertoires.length || selectedRepertoires.length > 8 || interval !== "" && (!Number.isInteger(Number(interval)) || Number(interval) < 60 || Number(interval) > 86400))) {
      setError("Choose an account, UTC date range, repertoires, speeds and rated policy. Recurrence must be 60–86400 seconds or left off.");
      return;
    }
    setBusy(true);
    try {
      const retry = pendingSave.current !== null;
      const pending = pendingSave.current ?? { operationId: crypto.randomUUID(),
        body: JSON.stringify({ expected_revision: loaded?.revision ?? 0, configuration: {
          workspace_id: workspaceId, provider, account: account.trim(),
          window: { start_at: new Date(start + "Z").toISOString(), end_at: new Date(end + "Z").toISOString(), precision: "millisecond" },
          repertoire_ids: selectedRepertoires, speeds: selectedSpeeds,
          rated: rated === "both" ? null : rated === "rated", interval_seconds: interval === "" ? null : Number(interval),
          revision: (loaded?.revision ?? 0) + 1,
        } }),
      };
      storePendingProductGameSync(scope, { kind: "save", ...pending });
      pendingSave.current = pending;
      setUnconfirmed(true);
      let response = retry
        ? await readOperationResponse(pending.operationId, { allowMissing: true, fetch: globalThis.fetch, baseUrl: API_URL })
        : new Response(null, { status: 404 });
      if (response.status === 404) response = await fetch(`${gameReviewBase}/configuration`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId }, body: pending.body,
      });
      if (response.status === 202 && (await response.clone().json() as { operation_id?: string }).operation_id !== pending.operationId)
        throw new Error("Game sync settings did not confirm the original save. Retry the original save to check its result.");
      response = await confirmOperationResponse(response, { fetch: globalThis.fetch, baseUrl: API_URL });
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          clearMutation(pending.operationId); pendingSave.current = null; setUnconfirmed(false);
        }
        throw new Error(response.status === 409 ? "Settings changed or a sync is still active. Refresh after it finishes before saving." : "Could not save game sync settings. Retry the original save to confirm its result.");
      }
      setLoaded(configurationSchema.parse(await response.json()));
      clearMutation(pending.operationId); pendingSave.current = null; setUnconfirmed(false);
      setMessage("Game sync settings saved.");
    } catch (failure) {
      if (failure instanceof FailedOperationError) {
        try { clearMutation(failure.operationId); pendingSave.current = null; setUnconfirmed(false); }
        catch { setError("The save result is known, but its original browser request could not be cleared. Reopen Settings to recover it."); return; }
      }
      setError(failure instanceof PendingOperationError ? "Game sync settings are still pending. Retry the original save to check its result."
        : failure instanceof Error ? failure.message : "Could not save settings. Retry the original save to confirm its result.");
    }
    finally { setBusy(false); }
  }

  async function run() {
    setBusy(true); setError(""); setMessage("");
    try {
      const pending = pendingRun.current ?? { provider, operation_id: crypto.randomUUID() };
      storePendingProductGameSync(scope, { kind: "run", ...pending });
      pendingRun.current = pending;
      setRunPending(true);
      const result = await client.sync(pendingRun.current);
      clearMutation(pending.operation_id); pendingRun.current = null; setRunPending(false);
      setMessage(`${provider === "lichess" ? "Lichess" : "Chess.com"} sync: ${result.status}.`);
    } catch (failure) {
      if (failure instanceof FailedOperationError) {
        try { clearMutation(failure.operationId); pendingRun.current = null; setRunPending(false); }
        catch { setError("The run result is known, but its original browser request could not be cleared. Reopen Settings to recover it."); return; }
      }
      setError(failure instanceof Error ? failure.message : "Could not request sync. Retry the original request to check its result.");
    }
    finally { setBusy(false); }
  }

  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter(item => item !== value) : [...values, value];
  const controlsDisabled = busy || unconfirmed || runPending || Boolean(recovery.error);
  return <section aria-label="Game sync settings">
    <h2>Game sync</h2>
    <p>Choose the games to import. Recurring sync uses this exact UTC window; change it when you want a different range.</p>
    <label>Game provider<select value={provider} disabled={controlsDisabled} onChange={event => setProvider(event.target.value as Provider)}>
      <option value="lichess">Lichess</option><option value="chesscom">Chess.com</option>
    </select></label>
    <label>Game account<input value={account} disabled={controlsDisabled} onChange={event => setAccount(event.target.value)} /></label>
    <label>Games from (UTC)<input type="datetime-local" value={start} disabled={controlsDisabled} onChange={event => setStart(event.target.value)} /></label>
    <label>Games until (UTC)<input type="datetime-local" value={end} disabled={controlsDisabled} onChange={event => setEnd(event.target.value)} /></label>
    <fieldset disabled={controlsDisabled}><legend>Game speeds</legend>{speeds.map(speed => <label key={speed}>
      <input type="checkbox" checked={selectedSpeeds.includes(speed)} onChange={() => setSpeeds(toggle(selectedSpeeds, speed))} />{speed}
    </label>)}</fieldset>
    <label>Rated games<select value={rated} disabled={controlsDisabled} onChange={event => setRated(event.target.value)}>
      <option value="">Choose…</option><option value="both">Rated and casual</option><option value="rated">Rated only</option>
      {provider === "lichess" && <option value="casual">Casual only</option>}
    </select></label>
    <fieldset disabled={controlsDisabled}><legend>Compare with repertoires</legend>{repertoires.map(repertoire => <label key={repertoire.id}>
      <input type="checkbox" checked={selectedRepertoires.includes(repertoire.id)} onChange={() => setRepertoires(toggle(selectedRepertoires, repertoire.id))} />{repertoire.name}
    </label>)}</fieldset>
    <label>Recurring sync interval (seconds; blank is off)<input type="number" min={60} max={86400} value={interval} disabled={controlsDisabled} onChange={event => setInterval(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <Button pending={busy} disabled={runPending || Boolean(recovery.error)} onClick={() => void save()}>{unconfirmed ? "Retry settings save" : "Save game sync settings"}</Button>
    <Button disabled={busy || !loaded || unconfirmed || Boolean(recovery.error)} onClick={() => void run()}>{runPending ? "Retry saved game sync" : "Run saved game sync"}</Button>
    <Button disabled={controlsDisabled} onClick={() => setGeneration(value => value + 1)}>Refresh game sync settings</Button>
  </section>;
}

export function CurrentGameSyncSettings() {
  const [repertoires, setRepertoires] = useState<{ id: string; name: string }[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/api/repertoires`, { signal: controller.signal, credentials: "same-origin" });
        if (!response.ok) throw new Error("unavailable");
        const result = z.object({ repertoires: z.array(z.object({ id: z.string(), name: z.string() })) }).parse(await response.json());
        if (!controller.signal.aborted) setRepertoires(result.repertoires);
      } catch { if (!controller.signal.aborted) setFailed(true); }
    })();
    return () => controller.abort();
  }, []);
  return failed ? <p role="alert">Game sync settings could not load repertoires. Reopen Settings to retry.</p>
    : repertoires === null ? <p role="status">Loading game sync settings…</p>
    : <GameSyncSettings workspaceId="legacy" repertoires={repertoires} />;
}
