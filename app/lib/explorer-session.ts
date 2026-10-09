import { z } from "zod";
import { API_URL } from "../const";
import { readLichessSessionToken, onLichessSessionTokenChange } from "./lichess-session";

export const explorerSessionStatusSchema = z.strictObject({
  status: z.enum(["registration_missing", "credential_rejected", "available"]),
});
export type ExplorerSessionStatus = z.infer<typeof explorerSessionStatusSchema>["status"];
const REJECTED_FINGERPRINT_KEY = "tempo-explorer-rejected-credential-v1";
let currentRegistration: { token: string; promise: Promise<ExplorerSessionStatus> } | null = null;

async function credentialFingerprint(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}

async function reconcileExplorerSession(token: string): Promise<ExplorerSessionStatus> {
  const fingerprint = await credentialFingerprint(token);
  if (sessionStorage.getItem(REJECTED_FINGERPRINT_KEY) === fingerprint) return "credential_rejected";
  const timeout = AbortSignal.timeout(5000);
  const endpoint = `${API_URL}/api/repertoire-coverage/explorer-session`;
  const response = await fetch(endpoint, { signal: timeout, headers: {
    "X-Tempo-Work-Class": "background", "Authorization": `Bearer ${token}`,
  } });
  if (!response.ok) throw new Error("Explorer connection status is unavailable. Retry when the service is available.");
  const { status } = explorerSessionStatusSchema.parse(await response.json());
  if (status === "credential_rejected") {
    // Keep this browser's known rejection across reloads and backend-session
    // loss. Only a different connected credential can clear this fingerprint.
    sessionStorage.setItem(REJECTED_FINGERPRINT_KEY, fingerprint);
    return status;
  }
  if (status === "available") return status;
  if (readLichessSessionToken() !== token) return "registration_missing";
  const registration = await fetch(endpoint, {
    method: "POST", signal: timeout,
    headers: { "Authorization": `Bearer ${token}`, "X-Tempo-Work-Class": "background" },
  });
  if (registration.status === 409) {
    const error = await registration.json() as { detail?: { code?: string } };
    if (error.detail?.code === "explorer_credential_rejected") {
      sessionStorage.setItem(REJECTED_FINGERPRINT_KEY, fingerprint);
      return "credential_rejected";
    }
  }
  if (!registration.ok || !(await registration.json() as { registered?: boolean }).registered) {
    throw new Error("Explorer connection could not be restored. Retry when the service is available.");
  }
  sessionStorage.removeItem(REJECTED_FINGERPRINT_KEY);
  return "available";
}

export async function ensureExplorerSession(): Promise<ExplorerSessionStatus> {
  const token = readLichessSessionToken();
  if (!token) return "registration_missing";
  if (currentRegistration?.token === token) return currentRegistration.promise;
  const registration = { token, promise: reconcileExplorerSession(token) };
  currentRegistration = registration;
  try { return await registration.promise; }
  finally { if (currentRegistration === registration) currentRegistration = null; }
}

export function startExplorerSessionRecovery(): () => void {
  const reconcile = () => {
    if (document.visibilityState === "visible") void ensureExplorerSession().catch(() => undefined);
  };
  reconcile();
  const interval = window.setInterval(reconcile, 60_000);
  const unsubscribe = onLichessSessionTokenChange(reconcile);
  window.addEventListener("online", reconcile);
  document.addEventListener("visibilitychange", reconcile);
  return () => {
    window.clearInterval(interval);
    unsubscribe();
    window.removeEventListener("online", reconcile);
    document.removeEventListener("visibilitychange", reconcile);
  };
}
