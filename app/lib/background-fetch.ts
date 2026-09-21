/** Fetch an API resource without allowing the request to outrank active UI work. */
export function backgroundFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Tempo-Work-Class", "background");
  return fetch(input, { ...init, headers });
}
