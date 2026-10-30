/** Compatibility boundary for an explicitly retired practice family, never puzzle themes. */
export function isRetiredPracticeCard(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return record.content_type === "endgame" || record.kind === "endgame";
}

export function withoutRetiredPractice<T>(cards: T[]): T[] {
  return cards.filter(card => !isRetiredPracticeCard(card));
}

/** Validate the original snapshot/checksum before calling this transformation. */
export function retirePortablePractice(tables: Record<string, Array<Record<string, unknown>>>): Record<string, Array<Record<string, unknown>>> {
  const retired = (tables.cards ?? []).filter(isRetiredPracticeCard);
  if (retired.some(card => typeof card.id !== "string" || !card.id))
    throw new Error("Retired card identity is missing. Keep the original snapshot.");
  const removed = new Set(retired.map(card => card.id));
  if ((tables.cards ?? []).some(card => card.repertoire_id === "__endgames__" && !removed.has(card.id)))
    throw new Error("The retired practice repertoire contains unrelated cards. Keep the original snapshot.");
  return Object.fromEntries(Object.entries(tables)
    .filter(([table]) => !["endgame_templates", "endgame_attempts", "tablebase_cache"].includes(table))
    .map(([table, rows]) => [table, rows.filter(row =>
      !(table === "cards" && removed.has(row.id)) && !removed.has(row.card_id) &&
      !(table === "repertoires" && row.id === "__endgames__") && row.repertoire_id !== "__endgames__")
      .map(row => table === "settings"
        ? Object.fromEntries(Object.entries(row).filter(([field]) => field !== "draw_hold_user_moves")) : row)]));
}

type MirrorRow = { key: string; value: unknown; updatedAt?: string };
/** Backup compatibility is a pure projection, applied only after validation/decryption. */
export function retireBackupPractice(stores: Record<string, MirrorRow[]>): Record<string, MirrorRow[]> {
  const facts = (row: MirrorRow) => row.value && typeof row.value === "object"
    ? row.value as Record<string, unknown> : {};
  const retired = (stores.cards ?? []).filter(row => isRetiredPracticeCard(row.value));
  if (retired.some(row => typeof facts(row).id !== "string" || !facts(row).id))
    throw new Error("Retired card identity is missing. Keep the original backup.");
  const removed = new Set(retired.map(row => facts(row).id));
  if ((stores.cards ?? []).some(row => facts(row).repertoire_id === "__endgames__" && !removed.has(facts(row).id)))
    throw new Error("The retired backup repertoire contains unrelated cards. Keep the original backup.");
  return Object.fromEntries(Object.entries(stores).map(([store, rows]) => [store, rows.filter(row =>
    !(store === "cards" && isRetiredPracticeCard(row.value)) && !removed.has(facts(row).card_id) &&
    !(store === "syncMetadata" && /^(endgame_templates|endgame_attempts|tablebase_cache):/.test(row.key)) &&
    !(store === "repertoires" && facts(row).id === "__endgames__"))
    .map(row => store === "settings" && row.value && typeof row.value === "object"
      ? { ...row, value: Object.fromEntries(Object.entries(row.value).filter(([field]) => field !== "draw_hold_user_moves")) } : row)]));
}
