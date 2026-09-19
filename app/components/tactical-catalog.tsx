import { useState } from "react";
import type { TacticalCatalog, TacticalPack } from "../lib/tactical-catalog";
import { packProgress } from "../lib/tactical-catalog";
import type { TacticProgress } from "../lib/tactics-progress";

export function TacticalCatalogPanel({
  catalog,
  progress,
  selectedPackId,
  onSelect,
  onActivate,
  busy,
}: {
  catalog: TacticalCatalog;
  progress: TacticProgress;
  selectedPackId: string;
  onSelect: (pack: TacticalPack) => void;
  onActivate: (ids: string[], active: boolean) => void;
  busy: boolean;
}) {
  const [expandedGroups, setExpandedGroups] = useState<string[]>(() => {
    try {
      return JSON.parse(
        localStorage.getItem("tempo-tactic-expanded-groups-v1") ?? '["basic"]',
      );
    } catch {
      return ["basic"];
    }
  });
  return (
    <section className="tactical-catalog" aria-label="Tactical puzzle catalog">
      <p>
        Activate packs to introduce up to your daily tactics limit. Due reviews
        continue after deactivation. Practicing any puzzle also adds it to daily
        reviews, in addition to automatic introductions.
      </p>
      {catalog.groups.map((group) => {
        const packs = catalog.packs.filter((pack) => pack.group === group.id);
        const clean = packs.reduce(
          (total, pack) => total + packProgress(pack, progress).clean,
          0,
        );
        const active = packs.filter((pack) => pack.active).length;
        return (
          <details
            key={group.id}
            open={expandedGroups.includes(group.id)}
            onToggle={(event) => {
              const open = event.currentTarget.open;
              setExpandedGroups((current) => {
                const next = open
                  ? [...new Set([...current, group.id])]
                  : current.filter((id) => id !== group.id);
                localStorage.setItem(
                  "tempo-tactic-expanded-groups-v1",
                  JSON.stringify(next),
                );
                return next;
              });
            }}
          >
            <summary>
              {group.name}
              <span>
                {clean} / {packs.length * 25} clean · {active} active packs
              </span>
              <progress
                aria-label={`${group.name} completion`}
                value={clean}
                max={packs.length * 25}
              />
            </summary>
            <div className="tactic-group-actions">
              <button
                disabled={busy}
                onClick={() =>
                  onActivate(
                    packs.map((pack) => pack.id),
                    true,
                  )
                }
              >
                Activate {packs.length} packs
              </button>
              <button
                disabled={busy || !active}
                onClick={() =>
                  onActivate(
                    packs.map((pack) => pack.id),
                    false,
                  )
                }
              >
                Deactivate {active} packs
              </button>
            </div>
            {catalog.themes
              .filter((theme) => theme.group === group.id)
              .map((theme) => {
                const themePacks = packs.filter(
                  (pack) => pack.theme === theme.id,
                );
                const themeClean = themePacks.reduce(
                  (total, pack) => total + packProgress(pack, progress).clean,
                  0,
                );
                return (
                  <details
                    key={theme.id}
                    className="tactic-theme"
                    open={
                      themePacks.some((pack) => pack.id === selectedPackId) ||
                      undefined
                    }
                  >
                    <summary>
                      {theme.name}
                      <span>
                        {themeClean} / {themePacks.length * 25} clean
                      </span>
                    </summary>
                    <div className="tactic-pack-grid">
                      {themePacks.map((pack) => (
                        <article
                          key={pack.id}
                          className={
                            selectedPackId === pack.id ? "selected" : ""
                          }
                        >
                          <button
                            className="tactic-pack-select"
                            aria-current={
                              selectedPackId === pack.id ? "true" : undefined
                            }
                            onClick={() => onSelect(pack)}
                          >
                            {pack.difficulty[0].toUpperCase() +
                              pack.difficulty.slice(1)}{" "}
                            · Pack {pack.ordinal}
                            <small>
                              {packProgress(pack, progress).clean} / 25 clean ·{" "}
                              {pack.minRating}–{pack.maxRating}
                            </small>
                            <small>
                              {pack.introduced} introduced · {pack.due} due
                            </small>
                          </button>
                          <button
                            aria-label={`${pack.active ? "Deactivate" : "Activate"} ${theme.name} ${pack.difficulty} pack ${pack.ordinal}`}
                            aria-pressed={pack.active}
                            disabled={busy}
                            onClick={() => onActivate([pack.id], !pack.active)}
                          >
                            {pack.active ? "Active" : "Activate"}
                          </button>
                        </article>
                      ))}
                    </div>
                  </details>
                );
              })}
          </details>
        );
      })}
    </section>
  );
}
