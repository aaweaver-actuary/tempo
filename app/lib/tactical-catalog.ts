import { z } from "zod";
import { API_URL, assetUrl } from "../const";
import { readWorkspaceData } from "./workspace-data";
import { usesLocalApi } from "../utils/local";
import type { TacticProgress } from "./tactics-progress";
export const tacticalCatalogSchema = z.object({
  version: z.literal(1),
  groups: z.array(z.object({ id: z.string(), name: z.string() })),
  themes: z.array(
    z.object({ id: z.string(), name: z.string(), group: z.string() }),
  ),
  packs: z.array(
    z.object({
      id: z.string(),
      theme: z.string(),
      group: z.string(),
      difficulty: z.string(),
      ordinal: z.number().int().positive(),
      count: z.literal(25),
      minRating: z.number(),
      maxRating: z.number(),
      asset: z.string(),
      legacyDeckId: z.string(),
      active: z.boolean().default(false),
      clean: z.number().default(0),
      introduced: z.number().default(0),
      due: z.number().default(0),
    }),
  ),
});
export type TacticalCatalog = z.infer<typeof tacticalCatalogSchema>;
export type TacticalPack = TacticalCatalog["packs"][number];
export function loadTacticalCatalog() {
  return readWorkspaceData(
    usesLocalApi()
      ? `${API_URL}/api/tactics/catalog`
      : assetUrl("data/tactics-catalog.json"),
    tacticalCatalogSchema,
  );
}
export async function setPackActivation(packIds: string[], active: boolean) {
  if (usesLocalApi()) {
    const response = await fetch(`${API_URL}/api/tactics/activation`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack_ids: packIds, active }),
    });
    if (!response.ok)
      throw new Error(
        `Could not update activation (HTTP ${response.status}). Retry.`,
      );
    return tacticalCatalogSchema.parse(await response.json());
  }
  const current: string[] = JSON.parse(
    localStorage.getItem("tempo-tactic-active-packs-v1") ?? "[]",
  );
  const next = new Set(current);
  for (const id of packIds) {
    if (active) next.add(id);
    else next.delete(id);
  }
  localStorage.setItem(
    "tempo-tactic-active-packs-v1",
    JSON.stringify([...next]),
  );
  const catalog = await loadTacticalCatalog();
  return {
    ...catalog,
    packs: catalog.packs.map((pack) => ({
      ...pack,
      active: next.has(pack.id),
    })),
  };
}
export function packProgress(pack: TacticalPack, progress: TacticProgress) {
  return (
    progress[pack.id] ?? {
      clean: pack.clean,
      index: 0,
      cleanIds: [],
      discoveredIds: [],
    }
  );
}
export async function migrateDemoProgress(
  catalog: TacticalCatalog,
  progress: TacticProgress,
) {
  if (localStorage.getItem("tempo-tactic-progress-pack-migration-v1"))
    return progress;
  const migrated = { ...progress };
  for (const pack of catalog.packs) {
    const legacy = progress[pack.legacyDeckId.replace("-", ":")];
    if (!legacy?.cleanIds?.length && !legacy?.discoveredIds?.length) continue;
    const response = await fetch(assetUrl(pack.asset));
    if (!response.ok)
      throw new Error("Could not migrate puzzle completion. Retry.");
    const records = z
      .array(z.object({ PuzzleId: z.string() }))
      .parse(await response.json());
    const identities = new Set(
      records.map((record) => `lichess-${record.PuzzleId}`),
    );
    const cleanIds = (legacy.cleanIds ?? []).filter((id) => identities.has(id));
    const discoveredIds = (
      legacy.discoveredIds ??
      legacy.cleanIds ??
      []
    ).filter((id) => identities.has(id));
    migrated[pack.id] = {
      clean: cleanIds.length,
      index: discoveredIds.length,
      cleanIds,
      discoveredIds,
    };
  }
  localStorage.setItem("tempo-tactics-progress-v2", JSON.stringify(migrated));
  localStorage.setItem("tempo-tactic-progress-pack-migration-v1", "true");
  return migrated;
}
