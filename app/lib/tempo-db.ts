export const TEMPO_DB_VERSION = 1;

import { portableSnapshotSchema, storedRecordSchema } from "../domain/schemas";
import { parseData, validRecords } from "./validated-data";

export const TEMPO_STORES = [
  "repertoires",
  "lines",
  "cards",
  "reviews",
  "queues",
  "annotations",
  "games",
  "settings",
  "syncMetadata",
  "tombstones",
] as const;

export type TempoStore = (typeof TEMPO_STORES)[number];
export type StoredRecord<T = unknown> = { key: string; value: T; updatedAt?: string };

let databasePromise: Promise<IDBDatabase> | undefined;

export function openTempoDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("tempo", TEMPO_DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      for (const store of TEMPO_STORES) {
        if (!database.objectStoreNames.contains(store)) database.createObjectStore(store, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return databasePromise;
}

export async function putRecords(store: TempoStore, records: StoredRecord[]): Promise<void> {
  const database = await openTempoDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(store, "readwrite");
    const target = transaction.objectStore(store);
    records.forEach((record) => target.put(record));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export async function getRecords(store: TempoStore): Promise<StoredRecord[]> {
  const database = await openTempoDatabase();
  return await new Promise((resolve, reject) => {
    const request = database.transaction(store).objectStore(store).getAll();
    request.onsuccess = () => resolve(validRecords(storedRecordSchema, request.result, `IndexedDB ${store}`));
    request.onerror = () => reject(request.error);
  });
}

export async function deleteRecords(store: TempoStore, keys: string[]): Promise<void> {
  if (!keys.length) return;
  const database = await openTempoDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(store, "readwrite");
    const target = transaction.objectStore(store);
    keys.forEach((key) => target.delete(key));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export type PortableSnapshot = {
  schemaVersion: number;
  exportedAt: string;
  source: string;
  tables: Record<string, Array<Record<string, unknown>>>;
  counts: Record<string, number>;
  checksum: string;
};

const TABLE_TO_STORE: Record<string, TempoStore> = {
  repertoires: "repertoires",
  repertoire_lines: "lines",
  cards: "cards",
  reviews: "reviews",
  daily_queue: "queues",
  daily_queue_days: "queues",
  position_annotations: "annotations",
  imported_games: "games",
  settings: "settings",
  game_sync_state: "syncMetadata",
  teaching_states: "syncMetadata",
  repertoire_cards: "syncMetadata",
  tactic_progress: "syncMetadata",
  endgame_templates: "syncMetadata",
  endgame_attempts: "syncMetadata",
  game_accounts: "syncMetadata",
  game_move_analysis: "syncMetadata",
  game_analysis_jobs: "syncMetadata",
  game_sync_jobs: "syncMetadata",
  game_derivation_jobs: "syncMetadata",
  game_position_occurrences: "syncMetadata",
  gameplay_card_priorities: "syncMetadata",
  repertoire_comparisons: "syncMetadata",
  game_repertoire_matches: "syncMetadata",
  game_findings: "syncMetadata",
  game_insight_recommendations: "syncMetadata",
};

function recordKey(table: string, row: Record<string, unknown>, index: number): string {
  if (table === "position_annotations") return `${row.repertoire_id}:${row.fen_key}`;
  if (table === "settings") return String(row.id ?? 1);
  if (table === "game_sync_state") return `game_sync_state:${row.provider ?? index}`;
  if (table === "teaching_states") return `teaching_states:${row.card_id}:${row.revision}:${row.ply}`;
  if (table === "daily_queue_days") return `daily_queue_days:${row.queue_date}`;
  if (table === "game_position_occurrences") return `game_position_occurrences:${row.game_id}:${row.ply}`;
  if (table === "game_repertoire_matches") return `game_repertoire_matches:${row.game_id}:${row.repertoire_id}`;
  if (table === "gameplay_card_priorities") return `gameplay_card_priorities:${row.card_id}`;
  if (TABLE_TO_STORE[table] === "syncMetadata") return `${table}:${row.id ?? row.card_id ?? row.game_id ?? row.provider ?? index}`;
  return String(row.id ?? `${table}:${index}`);
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
    .join(",")}}`;
}

async function snapshotChecksum(snapshot: PortableSnapshot): Promise<string> {
  const payload = stableJson({ schemaVersion: snapshot.schemaVersion, tables: snapshot.tables });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function importPortableSnapshot(snapshot: PortableSnapshot): Promise<Record<string, number>> {
  snapshot = parseData(portableSnapshotSchema, snapshot, "portable snapshot");
  if (snapshot.schemaVersion !== 1) throw new Error(`Unsupported snapshot version ${snapshot.schemaVersion}`);
  for (const [table, rows] of Object.entries(snapshot.tables)) {
    if (rows.length !== snapshot.counts[table]) throw new Error(`Snapshot count mismatch for ${table}`);
  }
  const checksum = await snapshotChecksum(snapshot);
  if (checksum !== snapshot.checksum) throw new Error("Snapshot checksum mismatch");
  const database = await openTempoDatabase();
  const stores = [...new Set(Object.keys(snapshot.tables).map((table) => TABLE_TO_STORE[table]).filter(Boolean))];
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(stores, "readwrite");
    for (const [table, rows] of Object.entries(snapshot.tables)) {
      const store = TABLE_TO_STORE[table];
      if (!store) continue;
      const target = transaction.objectStore(store);
      rows.forEach((row, index) => target.put({ key: recordKey(table, row, index), value: row, updatedAt: snapshot.exportedAt }));
    }
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
  const imported = Object.fromEntries(Object.entries(snapshot.tables).map(([table, rows]) => [table, rows.length]));
  localStorage.setItem("tempo-sqlite-migration", JSON.stringify({ completedAt: new Date().toISOString(), checksum, counts: imported }));
  return imported;
}
