import { retirePortablePractice } from "../../app/domain/retired-practice";
import { isRetiredPracticeCard } from "../../app/domain/retired-practice";
export const TEMPO_DB_VERSION = 1;

import { portableSnapshotSchema, storedRecordSchema } from "../../app/domain/schemas";
import { parseData, validRecords } from "../../app/lib/validated-data";

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
export type StoredRecord<T = unknown> = {
  key: string;
  value: T;
  updatedAt?: string;
};

let databasePromise: Promise<IDBDatabase> | undefined;

export function openTempoDatabase(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open("tempo", TEMPO_DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      for (const store of TEMPO_STORES) {
        if (!database.objectStoreNames.contains(store))
          database.createObjectStore(store, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return databasePromise;
}

export async function putRecords(
  store: TempoStore,
  records: StoredRecord[],
): Promise<void> {
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
    request.onsuccess = () =>
      resolve(
        validRecords(storedRecordSchema, request.result, `IndexedDB ${store}`),
      );
    request.onerror = () => reject(request.error);
  });
}

export async function deleteRecords(
  store: TempoStore,
  keys: string[],
): Promise<void> {
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
  card_revisions: "syncMetadata",
  prefix_splits: "syncMetadata",
  repertoire_cards: "syncMetadata",
  tactic_progress: "syncMetadata",
  tactic_captures: "syncMetadata",
  game_accounts: "syncMetadata",
  game_move_analysis: "syncMetadata",
  gameplay_events: "syncMetadata",
  guided_review_sessions: "syncMetadata",
  guided_review_attempts: "syncMetadata",
  game_feature_rows: "syncMetadata",
  daily_chess_snapshots: "syncMetadata",
  daily_chess_insights: "syncMetadata",
  game_analysis_jobs: "syncMetadata",
  game_sync_jobs: "syncMetadata",
  game_derivation_jobs: "syncMetadata",
  game_position_occurrences: "syncMetadata",
  gameplay_card_priorities: "syncMetadata",
  repertoire_comparisons: "syncMetadata",
  game_repertoire_matches: "syncMetadata",
  game_findings: "syncMetadata",
  threat_training_candidates: "syncMetadata",
  threat_analysis_requests: "syncMetadata",
  threat_candidate_requests: "syncMetadata",
  defense_attempts: "syncMetadata",
  game_insight_recommendations: "syncMetadata",
  repertoire_coverage_runs: "syncMetadata",
  repertoire_coverage_nodes: "syncMetadata",
  repertoire_coverage_candidates: "syncMetadata",
  explorer_position_cache: "syncMetadata",
};

function recordKey(
  table: string,
  row: Record<string, unknown>,
  index: number,
): string {
  if (table === "position_annotations")
    return `${row.repertoire_id}:${row.fen_key}`;
  if (table === "settings") return String(row.id ?? 1);
  if (table === "game_sync_state")
    return `game_sync_state:${row.provider ?? index}`;
  if (table === "teaching_states")
    return `teaching_states:${row.card_id}:${row.revision}:${row.ply}`;
  if (table === "daily_queue_days") return `daily_queue_days:${row.queue_date}`;
  if (table === "game_position_occurrences")
    return `game_position_occurrences:${row.game_id}:${row.ply}`;
  if (table === "game_repertoire_matches")
    return `game_repertoire_matches:${row.game_id}:${row.repertoire_id}`;
  if (table === "gameplay_card_priorities")
    return `gameplay_card_priorities:${row.card_id}`;
  if (TABLE_TO_STORE[table] === "syncMetadata")
    return `${table}:${row.id ?? row.card_id ?? row.game_id ?? row.provider ?? index}`;
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
  const payload = stableJson({
    schemaVersion: snapshot.schemaVersion,
    tables: snapshot.tables,
  });
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(payload),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function importPortableSnapshot(
  snapshot: PortableSnapshot,
): Promise<Record<string, number>> {
  snapshot = parseData(portableSnapshotSchema, snapshot, "portable snapshot");
  if (snapshot.schemaVersion !== 1)
    throw new Error(`Unsupported snapshot version ${snapshot.schemaVersion}`);
  for (const [table, rows] of Object.entries(snapshot.tables)) {
    if (rows.length !== snapshot.counts[table])
      throw new Error(`Snapshot count mismatch for ${table}`);
  }
  const checksum = await snapshotChecksum(snapshot);
  if (checksum !== snapshot.checksum)
    throw new Error("Snapshot checksum mismatch");
  snapshot = { ...snapshot, tables: retirePortablePractice(snapshot.tables) };
  const database = await openTempoDatabase();
  const stores = [
    ...new Set(
      Object.keys(snapshot.tables)
        .map((table) => TABLE_TO_STORE[table])
        .filter(Boolean),
    ),
  ];
  // This is an explicit, checksum-validated import, never cleanup during a read/open.
  // All affected mirror stores participate in this one import transaction; intents
  // and prepared phone reviews are in other databases and are never cleared here.
  const retirementStores: TempoStore[] = ["cards", "reviews", "queues", "syncMetadata", "repertoires"];
  if (!retirementStores.every(store => database.objectStoreNames.contains(store)))
    throw new Error("The browser mirror has an incompatible schema. Keep its data.");
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction([...new Set([...stores, ...retirementStores])], "readwrite");
    let retirementError: Error | undefined;
    const removed = new Set<unknown>();
    const cursor = transaction.objectStore("cards").openCursor();
    cursor.onsuccess = () => {
      const entry = cursor.result;
      if (entry) {
        const row = entry.value as StoredRecord<Record<string, unknown>>;
        if (isRetiredPracticeCard(row.value)) {
          if (typeof row.value.id !== "string" || !row.value.id) {
            retirementError = new Error("Retired mirror card identity is missing. Keep its data.");
            transaction.abort(); return;
          }
          removed.add(row.value.id); entry.delete();
        }
        else if (row.value?.repertoire_id === "__endgames__") {
          retirementError = new Error("The retired mirror repertoire contains unrelated cards. Keep its data.");
          transaction.abort(); return;
        }
        entry.continue(); return;
      }
      for (const store of retirementStores.filter(store => store !== "cards")) {
        const linked = transaction.objectStore(store).openCursor();
        linked.onsuccess = () => {
          const current = linked.result;
          if (!current) return;
          const row = current.value as StoredRecord<Record<string, unknown>>;
          if (removed.has(row.value?.card_id) ||
              (store === "syncMetadata" && /^(endgame_templates|endgame_attempts|tablebase_cache):/.test(row.key)) ||
              (store === "repertoires" && row.value?.id === "__endgames__")) current.delete();
          current.continue();
        };
      }
    };
    for (const [table, rows] of Object.entries(snapshot.tables)) {
      const store = TABLE_TO_STORE[table];
      if (!store) continue;
      const target = transaction.objectStore(store);
      rows.forEach((row, index) =>
        target.put({
          key: recordKey(table, row, index),
          value: row,
          updatedAt: snapshot.exportedAt,
        }),
      );
    }
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(retirementError ?? transaction.error);
  });
  const imported = Object.fromEntries(
    Object.entries(snapshot.tables).map(([table, rows]) => [
      table,
      rows.length,
    ]),
  );
  localStorage.setItem(
    "tempo-sqlite-migration",
    JSON.stringify({
      completedAt: new Date().toISOString(),
      checksum,
      counts: imported,
    }),
  );
  return imported;
}
