/** Fetch an API resource without allowing the request to outrank active UI work. */
export function backgroundFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Tempo-Work-Class", "background");
  return fetch(input, { ...init, headers });
}

/** Retry only the server's explicit foreground wait, inside the caller's deadline. */
export async function backgroundReadWhenAdmitted(input: RequestInfo | URL, signal: AbortSignal): Promise<Response> {
  for (;;) {
    signal.throwIfAborted();
    const response = await backgroundFetch(input, { signal });
    const retry = Number(response.headers.get("Retry-After"));
    if (response.status !== 503 || !Number.isFinite(retry) || retry <= 0) return response;
    const body = await response.clone().json().catch(() => null) as { detail?: string } | null;
    if (body?.detail !== "Waiting for foreground activity") return response;
    await new Promise<void>((resolve, reject) => {
      const done = () => { signal.removeEventListener("abort", abort); resolve(); };
      const timer = setTimeout(done, Math.min(retry, 60) * 1000);
      const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(signal.reason); };
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
}
