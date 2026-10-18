/** Render supplied source links only for supported providers' HTTPS game pages. */
export function gameSourceUrl(provider: unknown, supplied: unknown): string | null {
  if (typeof supplied !== 'string') return null;
  try {
    const url = new URL(supplied);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash) return null;
    if (provider === 'chess.com' && ['www.chess.com','chess.com'].includes(url.hostname) && /^\/game\/(live|daily)\/\d+$/.test(url.pathname)) return url.href;
    if (provider === 'lichess' && url.hostname === 'lichess.org' && /^\/[A-Za-z0-9]{8}(?:[A-Za-z0-9]{4})?$/.test(url.pathname)) return url.href;
  } catch { /* Unavailable provenance remains text, never a guessed link. */ }
  return null;
}
