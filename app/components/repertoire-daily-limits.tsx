"use client";
import { useCallback, useEffect, useState } from "react";
import { API_URL } from "../const";
import { repertoiresResponseSchema } from "../domain/schemas";
import { readWorkspaceResponse, invalidateWorkspaceData } from "../lib/workspace-data";
import { pendingRepertoireLimit, saveRepertoireLimit } from "../lib/repertoire-settings-save";
import { Button } from "./buttons/BaseButton";
import { TextInput } from "./inputs/TextInput";
import { SelectInput } from "./inputs/SelectInput";

type RepertoireLimit = { id: string; name: string; new_cards_per_day: number | null };

function RepertoireLimitRow({ repertoire, defaultLimit }: { repertoire: RepertoireLimit; defaultLimit: number }) {
  const [restored] = useState(() => {
    try { return { pending: pendingRepertoireLimit(repertoire.id), error: "" }; }
    catch (error) { return { pending: null, error: String(error) }; }
  });
  const initialLimit = restored.pending ? restored.pending.new_cards_per_day : repertoire.new_cards_per_day;
  const [inherit, setInherit] = useState(initialLimit === null);
  const [customLimit, setCustomLimit] = useState(String(initialLimit ?? defaultLimit));
  const [savedLimit, setSavedLimit] = useState(repertoire.new_cards_per_day);
  const [pending, setPending] = useState(restored.pending !== null || Boolean(restored.error));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(restored.error);
  const [status, setStatus] = useState("");
  const parsedLimit = Number(customLimit);
  const invalidLimit = !inherit && (customLimit.trim() === "" || !Number.isInteger(parsedLimit) || parsedLimit < 0 || parsedLimit > 100);
  const draftLimit = inherit ? null : parsedLimit;
  const dirty = draftLimit !== savedLimit;
  async function save() {
    setSaving(true); setError(""); setStatus("");
    try {
      const result = await saveRepertoireLimit(repertoire.id, draftLimit);
      setSavedLimit(result.new_cards_per_day);
      setInherit(result.new_cards_per_day === null);
      setCustomLimit(String(result.new_cards_per_day ?? defaultLimit));
      setPending(false);
      invalidateWorkspaceData();
      setStatus("Saved. Today’s remaining new cards will refresh.");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save this limit. Retry when the service is available.");
      try { setPending(pendingRepertoireLimit(repertoire.id) !== null); } catch { setPending(true); }
    } finally { setSaving(false); }
  }
  return <div className="repertoire-limit-row" role="group" aria-label={`${repertoire.name} daily limit`}>
    <strong>{repertoire.name}</strong>
    <label><span>Allowance</span><SelectInput aria-label={`${repertoire.name} allowance`} disabled={saving || pending}
      value={inherit ? "default" : "custom"} onChange={event => { setInherit(event.target.value === "default"); setStatus(""); }}>
      <option value="default">Use default ({defaultLimit}/day)</option><option value="custom">Custom limit</option>
    </SelectInput></label>
    {!inherit && <label><span>New cards per day</span><TextInput aria-label={`${repertoire.name} new cards per day`} type="number" min="0" max="100" step="1"
      disabled={saving || pending} value={customLimit} onChange={event => { setCustomLimit(event.target.value); setStatus(""); }} /></label>}
    <small>Current limit: {savedLimit ?? defaultLimit}/day{savedLimit === 0 ? " · New cards paused" : ""}</small>
    {invalidLimit && <p role="alert">Enter a whole number from 0 to 100.</p>}
    {pending && <p role="status">A save is awaiting confirmation. Check its result before editing.</p>}
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    <Button aria-label={`${pending ? "Check pending save for" : "Save limit for"} ${repertoire.name}`} pending={saving}
      disabled={saving || (!pending && (invalidLimit || !dirty))} onClick={() => void save()}>
      {pending ? "Check pending save" : "Save limit"}
    </Button>
  </div>;
}

export function RepertoireDailyLimits({ defaultLimit, enabled, local }: { defaultLimit: number; enabled: boolean; local: boolean }) {
  const [repertoires, setRepertoires] = useState<RepertoireLimit[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await readWorkspaceResponse(`${API_URL}/api/repertoires`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = repertoiresResponseSchema.parse(await response.json());
      setRepertoires(payload.repertoires.map(repertoire => {
        if (repertoire.new_cards_per_day === undefined) throw new Error("Upgrade the local service to use repertoire limits.");
        return { id: repertoire.id, name: repertoire.name, new_cards_per_day: repertoire.new_cards_per_day };
      }));
      setError("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Connection failed"); }
  }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => { if (enabled && local && !cancelled) void load(); });
    return () => { cancelled = true; };
  }, [enabled, local, load]);
  return <div className="repertoire-daily-limits">
    <h3>Repertoire limits</h3>
    <p>Unused allowance does not carry over. Due reviews are additional. Set a custom limit to 0 to pause new cards.</p>
    {!local ? <p>Repertoire overrides require local Tempo.</p> : !enabled ? <p>Load settings before editing repertoire limits.</p> : <>
      {error && <div role="alert"><p>Repertoire limits unavailable: {error}</p><Button onClick={() => { invalidateWorkspaceData(); void load(); }}>Retry repertoire limits</Button></div>}
      {!repertoires && !error && <p role="status">Loading repertoire limits…</p>}
      {repertoires?.length === 0 && <p>Import a repertoire to set its daily limit.</p>}
      {repertoires?.map(repertoire => <RepertoireLimitRow key={repertoire.id} repertoire={repertoire} defaultLimit={defaultLimit} />)}
    </>}
  </div>;
}
