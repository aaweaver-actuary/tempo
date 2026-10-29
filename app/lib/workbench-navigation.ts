import { resolveBoardAppearance, type BoardAppearance } from "@tempo/board-ui/appearance";

/** Public navigation only. No credentials or service calls cross this boundary. */
export function workbenchUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.hash ||
        [...url.searchParams.keys()].some(key => !["run", "category", "dismissal"].includes(key))) return null;
    return url.href;
  } catch { return null; }
}

/** Reject unsafe public build settings before Vite can embed them in assets. */
export function workbenchUrlForBuild(value: string | undefined): string {
  const parsed = workbenchUrl(value);
  if (value && !parsed) throw new Error("TEMPO_WORKBENCH_URL must be a public HTTP(S) URL with only run, category or dismissal query fields.");
  return parsed ?? "";
}

/** Appearance is a non-secret launch snapshot, independent of service access. */
export function workbenchLaunchUrl(value: string | undefined, appearance: BoardAppearance): string | null {
  const destination = workbenchUrl(value);
  if (!destination) return null;
  const url = new URL(destination);
  const selected = resolveBoardAppearance(appearance);
  url.searchParams.set("boardTheme", selected.theme);
  url.searchParams.set("pieceSet", selected.pieceSet);
  return url.href;
}
