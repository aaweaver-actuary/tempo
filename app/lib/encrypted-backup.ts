import { deleteRecords, getRecords, putRecords, TEMPO_STORES, type StoredRecord } from "./tempo-db";
import { encryptedEnvelopeSchema, decryptedBackupSchema, storedRecordSchema } from "../domain/schemas";
import { parseData, validRecords } from "./validated-data";
import { z } from "zod";

const queueOrderSchema = z.looseObject({ queue_date: z.string().optional(), position: z.number().int().nonnegative().optional() });
const tombstoneValueSchema = z.strictObject({ store: z.enum(TEMPO_STORES).optional(), recordKey: z.string().min(1).optional() });

type EncryptedSnapshot = {
  version: 1;
  algorithm: "PBKDF2-AES-GCM";
  iterations: number;
  salt: string;
  iv: string;
  ciphertext: string;
};

function bytesToBase64(bytes: Uint8Array) {
  let value = "";
  bytes.forEach((byte) => { value += String.fromCharCode(byte); });
  return btoa(value);
}

function base64ToBytes(value: string) {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}

async function backupKey(passphrase: string, salt: Uint8Array, iterations: number) {
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt: Uint8Array.from(salt), iterations }, material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export async function createEncryptedBackup(passphrase: string): Promise<Blob> {
  if (!passphrase) throw new Error("A passphrase is required");
  const stores = Object.fromEntries(await Promise.all(TEMPO_STORES.map(async (store) => [store, await getRecords(store)])));
  const plaintext = new TextEncoder().encode(JSON.stringify({ schemaVersion: 1, exportedAt: new Date().toISOString(), stores }));
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const iterations = 310_000;
  const key = await backupKey(passphrase, salt, iterations);
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext));
  const envelope: EncryptedSnapshot = { version: 1, algorithm: "PBKDF2-AES-GCM", iterations, salt: bytesToBase64(salt), iv: bytesToBase64(iv), ciphertext: bytesToBase64(ciphertext) };
  return new Blob([JSON.stringify(envelope)], { type: "application/vnd.tempo.backup+json" });
}

export async function decryptBackup(file: Blob, passphrase: string) {
  const envelope = parseData(encryptedEnvelopeSchema, JSON.parse(await file.text()), "encrypted backup");
  if (envelope.version !== 1 || envelope.algorithm !== "PBKDF2-AES-GCM") throw new Error("Unsupported backup format");
  const salt = base64ToBytes(envelope.salt);
  const iv = base64ToBytes(envelope.iv);
  const key = await backupKey(passphrase, salt, envelope.iterations);
  const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, base64ToBytes(envelope.ciphertext));
  return parseData(decryptedBackupSchema, JSON.parse(new TextDecoder().decode(plaintext)), "decrypted backup");
}

function newest(left: StoredRecord | undefined, right: StoredRecord): StoredRecord {
  if (!left) return right;
  const leftTime = Date.parse(left.updatedAt ?? "1970-01-01");
  const rightTime = Date.parse(right.updatedAt ?? "1970-01-01");
  return rightTime >= leftTime ? right : left;
}

export async function restoreEncryptedBackup(file: Blob, passphrase: string) {
  const snapshot = await decryptBackup(file, passphrase);
  if (snapshot.schemaVersion !== 1) throw new Error("Unsupported backup schema");
  let merged = 0;
  for (const store of TEMPO_STORES) {
    const incoming = validRecords(storedRecordSchema, snapshot.stores[store] ?? [], `backup ${store}`);
    const current = await getRecords(store);
    const records = new Map(current.map((record) => [record.key, record]));
    for (const record of incoming) {
      if (!record || typeof record.key !== "string") throw new Error(`Invalid ${store} record`);
      records.set(record.key, store === "settings" ? newest(records.get(record.key), record) : record);
      merged += 1;
    }
    if (store === "queues") {
      const ordered = [...records.values()].map((record) => ({ record, order: parseData(queueOrderSchema, record.value, `backup queue order ${record.key}`) })).sort((left, right) => {
        const a = left.order;
        const b = right.order;
        return String(a.queue_date ?? "").localeCompare(String(b.queue_date ?? "")) || Number(a.position ?? 0) - Number(b.position ?? 0) || left.record.key.localeCompare(right.record.key);
      }).map(({record}) => record);
      await putRecords(store, ordered);
    } else await putRecords(store, [...records.values()]);
  }
  const tombstones = await getRecords("tombstones");
  for (const tombstone of tombstones) {
    const value = parseData(tombstoneValueSchema, tombstone.value, `backup tombstone ${tombstone.key}`);
    const target = value.store;
    const key = value.recordKey;
    if (target && target !== "tombstones" && key) await deleteRecords(target, [key]);
  }
  localStorage.setItem("tempo-last-backup-restore", new Date().toISOString());
  return { merged };
}
