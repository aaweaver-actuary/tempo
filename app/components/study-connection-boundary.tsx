import { lazy, Suspense, useEffect, useState } from "react";
import { connectStudy, disconnectStudy, retainedStudyData, studyConnection } from "../lib/study-connection";
const Home = lazy(() => import("../views/home_view"));
const StudySettingsPanel = lazy(() => import("./study-settings-panel"));
declare const __TEMPO_WORKBENCH_URL__: string;

export default function StudyConnectionBoundary() {
  const prior = studyConnection();
  const [origin, setOrigin] = useState(prior?.origin ?? "");
  const [workspace, setWorkspace] = useState(prior?.workspace ?? "");
  const [credential, setCredential] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retained, setRetained] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => {
    let active = true;
    if (prior) void connectStudy(prior.origin, prior.workspace, prior.credential).then(() => { if (active) setReady(true); }).catch(error => {
      if (!active) return;
      // Cached configuration may render the shell offline, never authorize server settlement.
      if (!navigator.onLine && prior.bootstrap) setReady(true); else setError(String(error));
    });
    const expired = () => { setReady(false); setError("Learning access needs attention. Reconnect with the original workspace Study credential. Saved work is retained."); };
    window.addEventListener("tempo:study-auth-required", expired);
    return () => { active = false; window.removeEventListener("tempo:study-auth-required", expired); };
    // The connection is frozen for this mounted application lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (ready) return <><div className="study-connection-controls">
    <button onClick={() => setShowSettings(value => !value)}>Study settings</button>
    <button onClick={() => { disconnectStudy(); window.location.reload(); }}>Disconnect Learning</button>
    {typeof __TEMPO_WORKBENCH_URL__ !== "undefined" && __TEMPO_WORKBENCH_URL__ && <a href={__TEMPO_WORKBENCH_URL__} rel="noreferrer">Open Workbench</a>}
  </div><Suspense fallback={<p role="status">Loading Study…</p>}>{showSettings && <StudySettingsPanel />}<Home /></Suspense></>;
  return <main className="study-connect"><h1>Connect Study to Learning</h1>
    <p>Use the Learning workspace and its Study credential.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError("");
      try { await connectStudy(origin, workspace, credential); setCredential(""); setReady(true); }
      catch (error) { setError(String(error)); }
      finally { setBusy(false); }
    }}>
      <label>Learning origin<input required type="url" value={origin} onChange={event => setOrigin(event.target.value)} placeholder="http://127.0.0.1:8001" /></label>
      <label>Workspace<input required value={workspace} onChange={event => setWorkspace(event.target.value)} autoComplete="off" /></label>
      <label>Study credential<input required type="password" value={credential} onChange={event => setCredential(event.target.value)} autoComplete="off" /></label>
      <button disabled={busy}>{busy ? "Connecting…" : "Connect to Learning"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    <button onClick={() => void retainedStudyData().then(data => setRetained(JSON.stringify(data, null, 2))).catch(error => setError(String(error)))}>Inspect retained Study work</button>
    {retained && <><pre>{retained}</pre><a download="retained-study-work.json" href={`data:application/json;charset=utf-8,${encodeURIComponent(retained)}`}>Export retained Study work</a></>}
  </main>;
}
