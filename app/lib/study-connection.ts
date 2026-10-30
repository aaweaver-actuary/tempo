import { readAuthorityRegistry, readRetainedStudyData, readSessionConnection, removeSessionConnection,
  sessionConnectionAvailable, writeAuthorityRegistry, writeSessionConnection } from "../../database/browser/study-connection-storage";
import { holdActiveStudyRequest } from "./study-priority";
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
export class StudyConnectionError extends Error {
  constructor(readonly kind: "transport" | "http" | "bootstrap" | "authority" | "storage" | "input" | "superseded", message: string,
    readonly cachedPracticeAvailable = false, readonly status?: number, options?: ErrorOptions) {
    super(message, options); this.name = "StudyConnectionError";
  }
}
let connectionGeneration = 0;
let current: StudyConnection | undefined;
const authorityIdentity = (origin: string, workspace: string) => `${origin}:study:${encodeURIComponent(workspace)}`;
const offlineDatabaseFor = (identity: string) => `tempo-offline-training:${encodeURIComponent(identity)}`;
function validatedOrigin(origin: string) {
  const parsed = new URL(origin);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash)
    throw new Error("Enter the exact Learning origin (scheme, host and port).");
  return parsed.origin;
}
function validatedBootstrap(value: unknown, workspace: string) {
  const bootstrap = studyBootstrapSchema.parse(value);
  if (bootstrap.workspace_id !== workspace) throw new Error("Learning returned another workspace.");
  new Intl.DateTimeFormat("en-CA", { timeZone: bootstrap.timezone });
  return bootstrap;
}
export let LEARNING_URL = API_URL;
function restored() {
  if (!independentStudy || current || !sessionConnectionAvailable()) return;
  try {
    const value = JSON.parse(readSessionConnection() ?? "null");
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
  return independentStudy ? authorityIdentity(connection!.origin, connection!.workspace)
    : `${new URL(API_URL || "/", window.location.href).origin}:study`;
}
export function studyOfflineDatabaseName() {
  return independentStudy ? offlineDatabaseFor(studyWorkspace()) : "tempo-offline-training";
}
type Authority = { origin: string; workspace: string; offlineDatabase: string };
function authorities(): Record<string, Authority> {
  try {
    const value: unknown = JSON.parse(readAuthorityRegistry() ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid registry");
    for (const [identity, entry] of Object.entries(value)) {
      const authority = entry as Authority;
      if (!authority || typeof authority.origin !== "string" || typeof authority.workspace !== "string" || !authority.workspace.trim() ||
          validatedOrigin(authority.origin) !== authority.origin || authorityIdentity(authority.origin, authority.workspace) !== identity ||
          authority.offlineDatabase !== offlineDatabaseFor(identity)) throw new Error("Invalid authority binding");
    }
    return value as Record<string, Authority>;
  } catch (cause) {
    throw new StudyConnectionError("storage", "The saved Learning authority registry is incompatible. Keep browser data for recovery.", false, undefined, { cause });
  }
}
export function learningDayKey(date = new Date()) {
  const zone = studyConnection()?.bootstrap?.timezone;
  return zone ? new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date)
    : `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
}
const learningPaths = /^\/api\/(?:study\/(?:bootstrap|settings|activity)|queue\/(?:window|today|prepared|entries\/)|cards\/[^/]+\/(?:review|teaching|move-membership)|operations\/|opening-evidence\/|studies\/[^/]+\/exercises\/|tactics\/(?:attempt|progress)|defense-exercises\/)/;
/** Optional activity delivery cannot change authority or manufacture a Study request hold. */
export async function sendStudyActivity(session: string, remainingMs = 3000): Promise<void> {
  const connection = studyConnection();
  if (!connection) return;
  const response = await authenticatedLearningFetch(`${connection.origin}/api/study/activity?session_id=${encodeURIComponent(session)}&expires_in_ms=${remainingMs}`,
    {method: 'POST', signal: AbortSignal.timeout(500)}, connection, false);
  if (!response.ok) throw new Error(`Study host activity unavailable (HTTP ${response.status})`);
}
export function learningFetch(input: RequestInfo | URL, options: RequestInit = {}): Promise<Response> {
  const address = input instanceof Request ? input.url : String(input);
  const path = new URL(address, typeof window === "undefined" ? "http://tempo.local" : window.location.href).pathname;
  const background = new Headers(options.headers ?? (input instanceof Request ? input.headers : undefined)).get("X-Tempo-Work-Class") === "background";
  const essential = !background && /^\/api\/(queue\/|operations\/|cards\/[^/]+\/(review|teaching|move-membership)|tactics\/attempt|defense-exercises\/)/.test(path);
  const release = essential ? holdActiveStudyRequest() : () => {};
  try {
    const request = independentStudy ? authenticatedLearningFetch(input, options) : globalThis.fetch(input, options);
    return request.finally(release);
  } catch (error) { release(); throw error; }
}
async function authenticatedLearningFetch(input: RequestInfo | URL, options: RequestInit, connection = studyConnection(), notifyAuthentication = true): Promise<Response> {
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
  if (notifyAuthentication && (response.status === 401 || response.status === 403)) window.dispatchEvent(new Event("tempo:study-auth-required"));
  return response;
}

/** Composition supplies registered names; storage inspection owns its schema checks. */
export async function retainedStudyData(validateStorage = false) {
  return readRetainedStudyData(() => Object.values(authorities()).map(authority => authority.offlineDatabase), validateStorage);
}
type AuthorityTransition = "resume" | "recover" | "cutover";
export async function assertStudyAuthority(origin: string, workspace: string, transition: AuthorityTransition = "cutover") {
  const identity = authorityIdentity(origin, workspace);
  let retained: Awaited<ReturnType<typeof retainedStudyData>>;
  try { retained = await retainedStudyData(true); }
  catch (cause) { throw new StudyConnectionError("storage", String(cause), false, undefined, { cause }); }
  const registered = authorities();
  const recovering = transition !== "cutover" && Boolean(registered[identity]);
  const records = retained.databases["tempo-study-intents"].intents ?? [];
  const foreign = records.filter(value => {
    const record = value as { workspace?: string; state?: string };
    if (["committed", "discarded"].includes(record.state ?? "")) return false;
    if (record.workspace === identity) return false;
    return !recovering || !record.workspace || !registered[record.workspace];
  });
  const currentOffline = offlineDatabaseFor(identity);
  const foreignOffline = Object.entries(retained.databases).some(([name, stores]) => {
    if (!name.startsWith("tempo-offline-training") || name === currentOffline) return false;
    if (recovering && Object.values(registered).some(authority => authority.offlineDatabase === name)) return false;
    const attempts = stores.opening_attempts as { delivery_state?: string }[] | undefined;
    if (attempts?.some(attempt => !["complete", "rejected"].includes(attempt.delivery_state ?? ""))) return true;
    return stores.training?.some(value => {
      const prepared = value as { attempts?: { serverReviewId?: number; serverAcknowledged?: boolean }[] };
      return prepared.attempts?.some(attempt => !attempt.serverReviewId && !attempt.serverAcknowledged);
    });
  });
  if (foreign.length || Object.keys(retained.legacy).length || foreignOffline)
    throw new StudyConnectionError("authority", "Saved Study work belongs to another or unconfirmed authority. Keep browser data, inspect the retained records, and recover it with its original service before connecting here.");
  return retained;
}
export async function connectStudy(origin: string, workspace: string, credential: string) {
  const release = holdActiveStudyRequest();
  try { return await connectStudyWithAuthority(origin, workspace, credential); }
  finally { release(); }
}
async function connectStudyWithAuthority(origin: string, workspace: string, credential: string) {
  const generation = ++connectionGeneration;
  const previous = studyConnection();
  const requireCurrent = () => {
    if (generation !== connectionGeneration) throw new StudyConnectionError("superseded", "A newer Learning connection replaced this request.");
  };
  let candidate: StudyConnection;
  try {
    candidate = { origin: validatedOrigin(origin), workspace: workspace.trim(), credential };
    if (!candidate.workspace || credential.length < 32) throw new Error("Enter the workspace and its Study credential.");
  } catch (cause) { throw new StudyConnectionError("input", String(cause), false, undefined, { cause }); }
  const identity = authorityIdentity(candidate.origin, candidate.workspace);
  const transition: AuthorityTransition = previous
    ? authorityIdentity(previous.origin, previous.workspace) === identity ? "resume" : "cutover"
    : authorities()[identity] ? "recover" : "cutover";
  await assertStudyAuthority(candidate.origin, candidate.workspace, transition);
  requireCurrent();
  const failForTransport = async (cause: unknown): Promise<never> => {
    requireCurrent();
    await assertStudyAuthority(candidate.origin, candidate.workspace, transition);
    requireCurrent();
    let cachedPracticeAvailable = false;
    if (transition === "resume" && previous?.bootstrap && authorities()[identity]) {
      try {
        if (validatedOrigin(previous.origin) !== previous.origin) throw new Error("Invalid saved origin");
        validatedBootstrap(previous.bootstrap, previous.workspace);
        cachedPracticeAvailable = true;
      } catch (cause) { throw new StudyConnectionError("bootstrap", "The cached Study bootstrap is invalid. Reconnect to the original Learning service.", false, undefined, { cause }); }
    }
    throw new StudyConnectionError("transport", "Learning is unavailable. Reconnect to its original service; saved attempts await confirmation.", cachedPracticeAvailable, undefined, { cause });
  };
  const bootstrapSignal = AbortSignal.timeout(5_000);
  let response: Response;
  try {
    // Bootstrap errors notify only after the candidate's generation is checked.
    response = await authenticatedLearningFetch(`${candidate.origin}/api/study/bootstrap`, { signal: bootstrapSignal }, candidate, false);
  } catch (cause) {
    return failForTransport(cause);
  }
  requireCurrent();
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) window.dispatchEvent(new Event("tempo:study-auth-required"));
    throw new StudyConnectionError("http", `Learning connection was rejected (HTTP ${response.status}). Check the Study credential, workspace and allowed Origin.`, false, response.status);
  }
  // A consumed/locked body is a protocol error, not evidence of an outage.
  if (response.bodyUsed || response.body?.locked)
    throw new StudyConnectionError("bootstrap", "Learning returned an unavailable bootstrap response body. Reconnect to its original service.");
  let rawBootstrap: string;
  try { rawBootstrap = await response.text(); }
  catch (cause) {
    requireCurrent();
    if (bootstrapSignal.aborted || cause instanceof TypeError ||
        cause instanceof DOMException && ["AbortError", "TimeoutError"].includes(cause.name))
      return failForTransport(cause);
    throw new StudyConnectionError("bootstrap", String(cause), false, undefined, { cause });
  }
  requireCurrent();
  let bootstrap: StudyBootstrap;
  try { bootstrap = validatedBootstrap(JSON.parse(rawBootstrap), candidate.workspace); }
  catch (cause) { requireCurrent(); throw new StudyConnectionError("bootstrap", String(cause), false, undefined, { cause }); }
  requireCurrent();
  // Another tab may write while bootstrap is in flight. Preserve the transition,
  // rather than turning a cutover into recovery because a new registry appeared.
  await assertStudyAuthority(candidate.origin, candidate.workspace, transition);
  requireCurrent();
  try {
    const registered = authorities();
    writeAuthorityRegistry(JSON.stringify({ ...registered, [identity]: {
      origin: candidate.origin, workspace: candidate.workspace, offlineDatabase: offlineDatabaseFor(identity) } }));
    writeSessionConnection(JSON.stringify({ ...candidate, bootstrap }));
  } catch (cause) { throw new StudyConnectionError("storage", "Could not save the Learning connection. Keep browser data and restore storage.", false, undefined, { cause }); }
  current = { ...candidate, bootstrap }; LEARNING_URL = candidate.origin;
  return bootstrap;
}
export function disconnectStudy() { connectionGeneration++; removeSessionConnection(); current = undefined; LEARNING_URL = API_URL; }
