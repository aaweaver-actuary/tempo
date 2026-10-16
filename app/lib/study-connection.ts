import { z } from "zod";
import { API_URL } from "../const";

declare const __TEMPO_STUDY__: boolean;
export const independentStudy = typeof __TEMPO_STUDY__ !== "undefined" && __TEMPO_STUDY__;
export const studySettingsSchema = z.strictObject({
  study_only_mode: z.boolean(), new_cards_per_day: z.number().int().min(0).max(100),
  study_new_per_day: z.number().int().min(0).max(100), tactics_new_per_day: z.number().int().min(0).max(100),
  include_defensive_cards_in_daily_stack: z.boolean(), light_first_interval_days: z.number().int().min(1).max(90),
});
export const studyBootstrapSchema = z.strictObject({ schema_version: z.literal(1), workspace_id: z.string().min(1),
  timezone: z.string().min(1), local_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), settings: studySettingsSchema });
export type StudyBootstrap = z.infer<typeof studyBootstrapSchema>;
type StudyConnection = { origin: string; workspace: string; credential: string; bootstrap?: StudyBootstrap };
const connectionKey = "tempo-learning-connection-v1";
const authoritiesKey = "tempo-learning-authorities-v1";
let current: StudyConnection | undefined;
export let LEARNING_URL = API_URL;
function restored() {
  if (!independentStudy || current || typeof sessionStorage === "undefined") return;
  try {
    const value = JSON.parse(sessionStorage.getItem(connectionKey) ?? "null");
    if (value && typeof value.origin === "string" && typeof value.workspace === "string" && typeof value.credential === "string") {
      current = { ...value, bootstrap: value.bootstrap ? studyBootstrapSchema.parse(value.bootstrap) : undefined };
      LEARNING_URL = current!.origin;
    }
  } catch { /* The connection screen reports missing/unavailable session state. */ }
}
restored();
export function studyConnection() { restored(); return current; }
export function studyWorkspace() {
  const connection = studyConnection();
  if (independentStudy && !connection) throw new Error("Connect to Learning before opening Study storage.");
  return independentStudy ? `${connection!.origin}:study:${encodeURIComponent(connection!.workspace)}`
    : `${new URL(API_URL || "/", window.location.href).origin}:study`;
}
export function studyOfflineDatabaseName() {
  return independentStudy ? `tempo-offline-training:${encodeURIComponent(studyWorkspace())}` : "tempo-offline-training";
}
function authorities(): Record<string, { origin: string; workspace: string; offlineDatabase: string }> {
  const value: unknown = JSON.parse(localStorage.getItem(authoritiesKey) ?? "{}");
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.values(value).some(entry => !entry || typeof entry !== "object" || !["origin", "workspace", "offlineDatabase"].every(key => typeof (entry as Record<string, unknown>)[key] === "string")))
    throw new Error("The saved Learning authority registry is incompatible. Keep browser data for recovery.");
  return value as ReturnType<typeof authorities>;
}
export function learningDayKey(date = new Date()) {
  const zone = studyConnection()?.bootstrap?.timezone;
  return zone ? new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date)
    : `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
}
const learningPaths = /^\/api\/(?:study\/(?:bootstrap|settings)|queue\/(?:window|today|prepared|entries\/)|cards\/[^/]+\/(?:review|teaching|move-membership)|operations\/|opening-evidence\/|studies\/[^/]+\/exercises\/|tactics\/(?:attempt|progress)|defense-exercises\/|endgames\/)/;
export async function learningFetch(input: RequestInfo | URL, options: RequestInit = {}): Promise<Response> {
  if (!independentStudy) return globalThis.fetch(input, options);
  const connection = studyConnection();
  if (!connection) throw new Error("Connect to Learning before studying.");
  const original = input instanceof Request ? input.url : String(input);
  const url = new URL(original, connection.origin);
  if (!url.pathname.startsWith("/api/")) return globalThis.fetch(input, options);
  if (url.origin !== connection.origin || !learningPaths.test(url.pathname))
    throw new Error("This capability is unavailable in independent Study. Open Workbench.");
  const headers = new Headers(options.headers ?? (input instanceof Request ? input.headers : undefined));
  headers.delete("X-Tempo-Work-Class");
  headers.set("Authorization", `Bearer ${connection.credential}`);
  headers.set("X-Tempo-Workspace", connection.workspace);
  const delivery: RequestInit = { ...options, headers, credentials: "omit", redirect: "error", cache: "no-store" };
  const response = input instanceof Request
    ? await globalThis.fetch(new Request(input, delivery))
    : await globalThis.fetch(url, delivery);
  if (response.status === 401 || response.status === 403) window.dispatchEvent(new Event("tempo:study-auth-required"));
  return response;
}

/** Read existing stores without upgrading, rewriting or deleting retained data. */
export async function retainedStudyData() {
  if (!globalThis.indexedDB) throw new Error("Durable Study storage is unavailable. Enable browser storage and retry.");
  const databases: Record<string, Record<string, unknown[]>> = {};
  const registered = authorities();
  const discovered = typeof indexedDB.databases === "function" ? (await indexedDB.databases()).map(database => database.name).filter((name): name is string => Boolean(name?.startsWith("tempo-offline-training:"))) : [];
  for (const name of new Set(["tempo-study-intents", "tempo-offline-training", ...Object.values(registered).map(authority => authority.offlineDatabase), ...discovered])) {
    databases[name] = await new Promise((resolve, reject) => {
      const request = indexedDB.open(name);
      let absent = false;
      request.onupgradeneeded = () => { absent = true; request.transaction!.abort(); };
      request.onerror = () => absent ? resolve({}) : reject(request.error);
      request.onblocked = () => reject(new Error("Close older Tempo tabs before inspecting saved Study work."));
      request.onsuccess = async () => {
        const database = request.result;
        try {
          const names = [...database.objectStoreNames];
          if (!names.length) throw new Error("Saved Study storage has an incompatible schema. Keep browser data.");
          const transaction = database.transaction(names, "readonly");
          const values = await Promise.all(names.map(store => new Promise<[string, unknown[]]>((read, fail) => {
            const result = transaction.objectStore(store).getAll();
            result.onsuccess = () => read([store, result.result]); result.onerror = () => fail(result.error);
          })));
          transaction.oncomplete = () => resolve(Object.fromEntries(values));
          transaction.onabort = () => reject(transaction.error ?? new Error("Saved Study inspection was aborted."));
        } catch (error) { reject(error); } finally { database.close(); }
      };
    });
  }
  const legacy = Object.fromEntries(Object.keys(localStorage).filter(key =>
    /^tempo-(?:pending-(?:training|teaching|endgame|burial)|bury-(?:operation|identity)|seen-moves|first-clean-passes)/.test(key))
    .map(key => [key, localStorage.getItem(key)]).filter(([,value]) => value && value !== "[]"));
  return { databases, legacy };
}
export async function assertStudyAuthority(origin: string, workspace: string) {
  const identity = `${origin}:study:${encodeURIComponent(workspace)}`;
  const retained = await retainedStudyData();
  const records = retained.databases["tempo-study-intents"].intents ?? [];
  const foreign = records.filter(value => {
    const record = value as { workspace?: string; state?: string };
    return record.workspace !== identity && !["committed", "discarded"].includes(record.state ?? "");
  });
  const currentOffline = `tempo-offline-training:${encodeURIComponent(identity)}`;
  const foreignOffline = Object.entries(retained.databases).some(([name, stores]) => {
    if (!name.startsWith("tempo-offline-training") || name === currentOffline) return false;
    const attempts = stores.opening_attempts as { delivery_state?: string }[] | undefined;
    if (attempts?.some(attempt => !["complete", "rejected"].includes(attempt.delivery_state ?? ""))) return true;
    return stores.training?.some(value => {
      const prepared = value as { attempts?: { serverReviewId?: number; serverAcknowledged?: boolean }[] };
      return prepared.attempts?.some(attempt => !attempt.serverReviewId && !attempt.serverAcknowledged);
    });
  });
  if (foreign.length || Object.keys(retained.legacy).length || foreignOffline)
    throw new Error("Saved Study work belongs to another or unconfirmed authority. Keep browser data, inspect the retained records, and recover it with its original service before connecting here.");
  return retained;
}
export async function connectStudy(origin: string, workspace: string, credential: string) {
  const parsed = new URL(origin);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash)
    throw new Error("Enter the exact Learning origin (scheme, host and port).");
  if (!workspace.trim() || credential.length < 32) throw new Error("Enter the workspace and its Study credential.");
  await assertStudyAuthority(parsed.origin, workspace.trim());
  const candidate = { origin: parsed.origin, workspace: workspace.trim(), credential };
  const previous = current;
  current = candidate; LEARNING_URL = candidate.origin;
  try {
    const response = await learningFetch(`${candidate.origin}/api/study/bootstrap`, { signal: AbortSignal.timeout(5_000) });
    if (!response.ok) throw new Error(`Learning connection was rejected (HTTP ${response.status}). Check the Study credential, workspace and allowed Origin.`);
    const bootstrap = studyBootstrapSchema.parse(await response.json());
    if (bootstrap.workspace_id !== candidate.workspace) throw new Error("Learning returned another workspace.");
    new Intl.DateTimeFormat("en-CA", { timeZone: bootstrap.timezone });
    current = { ...candidate, bootstrap };
    const registered = authorities();
    localStorage.setItem(authoritiesKey, JSON.stringify({ ...registered, [studyWorkspace()]: {
      origin: candidate.origin, workspace: candidate.workspace, offlineDatabase: studyOfflineDatabaseName() } }));
    sessionStorage.setItem(connectionKey, JSON.stringify(current));
    return bootstrap;
  } catch (error) { current = previous; LEARNING_URL = previous?.origin ?? API_URL; throw error; }
}
export function disconnectStudy() { sessionStorage.removeItem(connectionKey); current = undefined; LEARNING_URL = API_URL; }
