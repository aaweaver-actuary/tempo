const SESSION_TOKEN_KEY = "tempo-lichess-token";
const TOKEN_CHANGED_EVENT = "tempo:lichess-token-changed";

export function readLichessSessionToken(): string {
  if (typeof window === "undefined") return "";
  const sessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
  const legacyToken = localStorage.getItem(SESSION_TOKEN_KEY);
  if (legacyToken) {
    if (!sessionToken) sessionStorage.setItem(SESSION_TOKEN_KEY, legacyToken);
    localStorage.removeItem(SESSION_TOKEN_KEY);
  }
  return sessionStorage.getItem(SESSION_TOKEN_KEY) ?? "";
}

export function saveLichessSessionToken(token: string): void {
  sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  localStorage.removeItem(SESSION_TOKEN_KEY);
  window.dispatchEvent(new Event(TOKEN_CHANGED_EVENT));
}

export function clearLichessSessionToken(): void {
  sessionStorage.removeItem(SESSION_TOKEN_KEY);
  localStorage.removeItem(SESSION_TOKEN_KEY);
  window.dispatchEvent(new Event(TOKEN_CHANGED_EVENT));
}

export function onLichessSessionTokenChange(listener: () => void): () => void {
  window.addEventListener(TOKEN_CHANGED_EVENT, listener);
  return () => window.removeEventListener(TOKEN_CHANGED_EVENT, listener);
}
