// Persist one Docker engine command until PostgreSQL confirms its receipt.

import { randomUUID } from "node:crypto";
import { readFile, rename, unlink, writeFile } from "node:fs/promises";
import { requestMaiaApi } from "./maia-coverage-client.mjs";

export function createDurableEngineRequest(apiUrl, journalPath, transport = requestMaiaApi) {
  async function pending() {
    try {
      const value = JSON.parse(await readFile(journalPath, "utf8"));
      if (!value || typeof value.path !== "string" || typeof value.operationId !== "string" ||
          value.options?.method !== "POST") throw new Error("Engine command journal is invalid");
      return value;
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw error;
    }
  }

  async function send(path, options = {}) {
    if (options.method !== "POST") return transport(apiUrl, path, options);
    let saved = await pending();
    if (saved && (saved.path !== path || saved.options.body !== options.body)) {
      const error = new Error(`Engine command ${saved.operationId} must be resolved first`);
      error.operationId = saved.operationId;
      throw error;
    }
    if (!saved) {
      saved = { path, options, operationId: randomUUID() };
      const temporaryPath = `${journalPath}.${process.pid}.partial`;
      await writeFile(temporaryPath, `${JSON.stringify(saved)}\n`, { mode: 0o600 });
      await rename(temporaryPath, journalPath);
    }
    try {
      const result = await transport(apiUrl, path, {
        ...saved.options, operationId: saved.operationId,
      });
      await unlink(journalPath);
      return result;
    } catch (error) {
      if (error.terminal) await unlink(journalPath);
      throw error;
    }
  }

  async function recover() {
    const saved = await pending();
    if (!saved) return null;
    return { path: saved.path, result: await send(saved.path, saved.options) };
  }

  return { send, recover, pending };
}

export async function migrateLegacyDefenseClaimJournal(journalPath) {
  let saved;
  try { saved = JSON.parse(await readFile(journalPath, "utf8")); }
  catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  if (saved?.path !== "/api/defensive-threats/analysis/claim") return;
  const defensePath = `${journalPath}.defense`;
  try {
    await readFile(defensePath);
    throw new Error("Both legacy and dedicated defensive claim journals exist; preserve both for recovery");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await rename(journalPath, defensePath);
}
