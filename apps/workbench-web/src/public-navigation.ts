/** General navigation never asserts live card identity or asks Study to admit it. */
export function studyUrl(value: string | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        url.href !== url.origin + "/") return null;
    return url.href;
  } catch { return null; }
}

export function studyUrlForBuild(value: string | undefined): string {
  const parsed = studyUrl(value);
  if (value && !parsed) throw new Error("TEMPO_STUDY_URL must be a public HTTP(S) origin with no path, query or fragment.");
  return parsed ?? "";
}

export function studyHandoffNotice(category: string): string {
  const authority = category === "repertoire_miss"
    ? "This snapshot’s card identity has not been verified against that service."
    : category === "practical_gap"
      ? "This gap does not identify an existing Study card."
      : "This finding does not establish a matching Study card.";
  return `Study opens its normal queue. ${authority}`;
}


/** Public API origin is a form default, never a page URL or an auto-login. */
export function intelligenceOrigin(value: string | undefined): string | null {
  const parsed = studyUrl(value);
  return parsed ? new URL(parsed).origin : null;
}

export function intelligenceOriginForBuild(value: string | undefined): string {
  const parsed = intelligenceOrigin(value);
  if (value && !parsed) throw new Error("TEMPO_INTELLIGENCE_ORIGIN must be a public HTTP(S) origin with no path, query or fragment.");
  return parsed ?? "";
}
