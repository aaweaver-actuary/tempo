import { useState } from "react";
import type { View } from "@/app/types";
import { preloadView } from "../lib/workspace-data";

export const WORKSPACES: { id: View; label: string }[] = [
  { id: "train", label: "Train" },
  { id: "tactics", label: "Tactics" },
  { id: "endgames", label: "Endgames" },
  { id: "repertoire", label: "Repertoire" },
  { id: "builder", label: "Builder" },
  { id: "games", label: "Games" },
  { id: "progress", label: "Progress" },
  { id: "statistics", label: "Statistics" },
  { id: "settings", label: "Settings" },
];
const PHONE_PRIMARY: View[] = ["train", "tactics", "endgames", "builder"];
export default function Navbar({
  view,
  setView,
}: {
  view: View;
  setView: (view: View) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const select = (next: View) => {
    setMenuOpen(false);
    setView(next);
    window.scrollTo({ top: 0 });
  };
  const destination = (
    item: (typeof WORKSPACES)[number],
    className?: string,
  ) => (
    <button
      key={item.id}
      className={className}
      aria-current={view === item.id ? "page" : undefined}
      onPointerEnter={() => void preloadView(item.id).catch(() => undefined)}
      onFocus={() => void preloadView(item.id).catch(() => undefined)}
      onClick={() => select(item.id)}
    >
      {item.label}
    </button>
  );
  return (
    <nav
      className="nav"
      aria-label="Primary navigation"
      onKeyDown={(event) => {
        if (event.key === "Escape" && menuOpen) {
          event.preventDefault();
          event.stopPropagation();
          setMenuOpen(false);
          [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[aria-expanded="true"]')]
            .find(button => button.getClientRects().length > 0)?.focus();
        }
      }}
    >
      <div className="desktop-navigation">
        {WORKSPACES.map((item) => destination(item))}
      </div>
      <div className="phone-navigation">
        {WORKSPACES.filter((item) => PHONE_PRIMARY.includes(item.id)).map(
          (item) => destination(item),
        )}
        <button
          aria-expanded={menuOpen}
          aria-controls="workspace-menu"
          aria-current={!PHONE_PRIMARY.includes(view) ? "page" : undefined}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          More
        </button>
      </div>
      <button
        className="tablet-navigation"
        aria-expanded={menuOpen}
        aria-controls="workspace-menu"
        onClick={() => setMenuOpen(!menuOpen)}
      >
        {WORKSPACES.find((item) => item.id === view)?.label} ▾
      </button>
      {menuOpen && (
        <div
          id="workspace-menu"
          className="workspace-menu"
          aria-label="All sections"
        >
          {WORKSPACES.map((item) =>
            destination(
              item,
              PHONE_PRIMARY.includes(item.id)
                ? "phone-menu-primary"
                : undefined,
            ),
          )}
        </div>
      )}
    </nav>
  );
}
