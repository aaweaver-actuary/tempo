import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";
import { importPortableSnapshot, type PortableSnapshot } from "./tempo-db";

export async function migrateSqliteToBrowser(force = false) {
  if (!usesLocalApi()) return { status: "unavailable" as const };
  if (!force && localStorage.getItem("tempo-sqlite-migration")) return { status: "complete" as const };
  const response = await fetch(`${API_URL}/api/migration/snapshot`);
  if (!response.ok) throw new Error("The local database snapshot could not be read");
  const snapshot = (await response.json()) as PortableSnapshot;
  const counts = await importPortableSnapshot(snapshot);
  return { status: "migrated" as const, counts, checksum: snapshot.checksum };
}

