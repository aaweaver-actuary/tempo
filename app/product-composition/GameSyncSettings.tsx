import { useEffect, useState } from "react";
import { z } from "zod";
import { Button } from "../components/buttons/BaseButton";
import { createProductGameReviewApi, gameReviewBase } from "./game-review";
import { API_URL } from "../const";

const configurationSchema = z.object({
  workspace_id: z.string(), provider: z.enum(["lichess", "chesscom"]), account: z.string().min(1),
  window: z.object({ start_at: z.string(), end_at: z.string(), precision: z.string() }),
  repertoire_ids: z.array(z.string()).min(1), speeds: z.array(z.string()).min(1),
  rated: z.boolean().nullable(), interval_seconds: z.number().nullable(), revision: z.number().int(),
});
type Configuration = z.infer<typeof configurationSchema>;
type Provider = Configuration["provider"];
const speeds = ["bullet", "blitz", "rapid", "classical"] as const;
const client = createProductGameReviewApi((...argumentsList) => globalThis.fetch(...argumentsList));

export function GameSyncSettings({ workspaceId, repertoires }: {
  workspaceId: string; repertoires: readonly { id: string; name: string }[];
}) {
  const [provider, setProvider] = useState<Provider>("lichess");
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

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setMessage("");
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
  }, [provider, generation]);

  async function save() {
    setMessage(""); setError("");
    const startTime = Date.parse(start + "Z"), endTime = Date.parse(end + "Z");
    if (!account.trim() || !Number.isFinite(startTime) || !Number.isFinite(endTime) || startTime >= endTime || !rated ||
        !selectedSpeeds.length || !selectedRepertoires.length || selectedRepertoires.length > 8 || interval !== "" && (!Number.isInteger(Number(interval)) || Number(interval) < 60 || Number(interval) > 86400)) {
      setError("Choose an account, UTC date range, repertoires, speeds and rated policy. Recurrence must be 60–86400 seconds or left off.");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${gameReviewBase}/configuration`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: loaded?.revision ?? 0, configuration: {
          workspace_id: workspaceId, provider, account: account.trim(),
          window: { start_at: new Date(start + "Z").toISOString(), end_at: new Date(end + "Z").toISOString(), precision: "millisecond" },
          repertoire_ids: selectedRepertoires, speeds: selectedSpeeds,
          rated: rated === "both" ? null : rated === "rated", interval_seconds: interval === "" ? null : Number(interval),
          revision: (loaded?.revision ?? 0) + 1,
        } }),
      });
      if (!response.ok) throw new Error(response.status === 409 ? "Settings changed or a sync is still active. Refresh after it finishes before saving." : "Could not save game sync settings.");
      setLoaded(configurationSchema.parse(await response.json()));
      setMessage("Game sync settings saved.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not save settings."); }
    finally { setBusy(false); }
  }

  async function run() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await client.sync({ provider, operation_id: crypto.randomUUID() });
      setMessage(`${provider === "lichess" ? "Lichess" : "Chess.com"} sync: ${result.status}.`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not request sync."); }
    finally { setBusy(false); }
  }

  const toggle = (values: string[], value: string) => values.includes(value) ? values.filter(item => item !== value) : [...values, value];
  return <section aria-label="Game sync settings">
    <h2>Game sync</h2>
    <p>Choose the games to import. Recurring sync uses this exact UTC window; change it when you want a different range.</p>
    <label>Game provider<select value={provider} disabled={busy} onChange={event => setProvider(event.target.value as Provider)}>
      <option value="lichess">Lichess</option><option value="chesscom">Chess.com</option>
    </select></label>
    <label>Game account<input value={account} disabled={busy} onChange={event => setAccount(event.target.value)} /></label>
    <label>Games from (UTC)<input type="datetime-local" value={start} disabled={busy} onChange={event => setStart(event.target.value)} /></label>
    <label>Games until (UTC)<input type="datetime-local" value={end} disabled={busy} onChange={event => setEnd(event.target.value)} /></label>
    <fieldset disabled={busy}><legend>Game speeds</legend>{speeds.map(speed => <label key={speed}>
      <input type="checkbox" checked={selectedSpeeds.includes(speed)} onChange={() => setSpeeds(toggle(selectedSpeeds, speed))} />{speed}
    </label>)}</fieldset>
    <label>Rated games<select value={rated} disabled={busy} onChange={event => setRated(event.target.value)}>
      <option value="">Choose…</option><option value="both">Rated and casual</option><option value="rated">Rated only</option>
      {provider === "lichess" && <option value="casual">Casual only</option>}
    </select></label>
    <fieldset disabled={busy}><legend>Compare with repertoires</legend>{repertoires.map(repertoire => <label key={repertoire.id}>
      <input type="checkbox" checked={selectedRepertoires.includes(repertoire.id)} onChange={() => setRepertoires(toggle(selectedRepertoires, repertoire.id))} />{repertoire.name}
    </label>)}</fieldset>
    <label>Recurring sync interval (seconds; blank is off)<input type="number" min={60} max={86400} value={interval} disabled={busy} onChange={event => setInterval(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <Button pending={busy} onClick={() => void save()}>Save game sync settings</Button>
    <Button disabled={busy || !loaded} onClick={() => void run()}>Run saved game sync</Button>
    <Button disabled={busy} onClick={() => setGeneration(value => value + 1)}>Refresh game sync settings</Button>
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
