import { API_URL } from "../../app/const";
import { usesLocalApi } from "../../app/utils/local";
import { importPortableSnapshot } from "./tempo-db";
import { portableSnapshotSchema } from "../../app/domain/schemas";
import { readJsonResponse } from "../../app/lib/validated-data";

export async function migrateSqliteToBrowser(force = false) {
  if (!usesLocalApi()) return { status: "unavailable" as const };
  if (!force && localStorage.getItem("tempo-sqlite-migration")) return { status: "complete" as const };
  const response = await fetch(`${API_URL}/api/migration/snapshot`);
  if (!response.ok) throw new Error("The local database snapshot could not be read");
  const snapshot = await readJsonResponse(response, portableSnapshotSchema, "SQLite migration snapshot");
  const counts = await importPortableSnapshot(snapshot);
  return { status: "migrated" as const, counts, checksum: snapshot.checksum };
}
