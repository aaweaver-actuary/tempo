import { useEffect, useState } from "react";
import { LEARNING_URL, learningFetch, studyConnection, studySettingsSchema } from "../lib/study-connection";
import { executeStudyRequest, retainedStudyRequest } from "../lib/study-command-client";
import type { z } from "zod";
type Settings = z.infer<typeof studySettingsSchema>;
const labels: Record<keyof Settings, string> = {
  study_only_mode: "Pause optional background work",
  new_cards_per_day: "New opening cards per day", study_new_per_day: "New Study exercises per day",
  tactics_new_per_day: "New tactics per day", include_defensive_cards_in_daily_stack: "Include defensive cards",
  light_first_interval_days: "Light first interval in days",
};
export default function StudySettingsPanel() {
  const [settings, setSettings] = useState<Settings>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    let active = true;
    void learningFetch(`${LEARNING_URL}/api/study/settings`).then(async response => {
      if (!response.ok) throw new Error("Learning settings are unavailable. Retry when connected.");
      const values = studySettingsSchema.parse(await response.json());
      if (active) setSettings(values);
    }).catch(error => { if (active) setError(String(error)); });
    return () => { active = false; };
  }, []);
  return <section className="study-connect" aria-label="Study settings"><h2>Study settings</h2>
    <p>Study day: {studyConnection()?.bootstrap?.timezone}</p>
    {settings && <form onSubmit={async event => {
      event.preventDefault(); setSaving(true); setConfirmed(false); setError("");
      try {
        const retained = await retainedStudyRequest("settings");
        const pending = retained && retained.state !== "committed";
        const response = await executeStudyRequest({ scope: "settings", kind: "settings-command",
          requestId: pending ? retained.requestId : crypto.randomUUID(),
          endpoint: `${LEARNING_URL}/api/study/settings`, body: pending ? retained.body : settings });
        setSettings(studySettingsSchema.parse(await response.json())); setConfirmed(true);
        window.dispatchEvent(new Event("tempo:study-mode-changed"));
      } catch (error) { setError(String(error)); } finally { setSaving(false); }
    }}>
      {(Object.keys(labels) as (keyof Settings)[]).map(field => <label key={field}>{labels[field]}
        {typeof settings[field] === "boolean" ? <input type="checkbox" disabled={saving} checked={Boolean(settings[field])}
          onChange={event => setSettings({ ...settings, [field]: event.target.checked })} />
          : <input type="number" required disabled={saving} min={field === "light_first_interval_days" ? 1 : 0}
            max={field === "light_first_interval_days" ? 90 : 100} value={Number(settings[field])}
            onChange={event => setSettings({ ...settings, [field]: Number(event.target.value) })} />}
      </label>)}
      <button disabled={saving}>{saving ? "Saving…" : "Save Study settings"}</button>
    </form>}
    {error && <p role="alert">{error}</p>}{confirmed && <p role="status">Study settings saved by Learning.</p>}
  </section>;
}
