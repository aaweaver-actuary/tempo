import { canOpenWorkspace } from '../lib/study-workspace-policy';
import { independentStudy } from "../lib/study-connection";
import { Button } from "./buttons/BaseButton";
import { useRef, useState } from "react";
import type { View } from "@/app/types";
import { preloadView } from "../lib/workspace-data";

import { usePopupKeyboard } from "../lib/keyboard-shortcuts";

export const WORKSPACES: { id: View; label: string }[] = [
  { id: "train", label: "Train" },
  { id: "tactics", label: "Tactics" },
  { id: "endgames", label: "Endgames" },
  { id: "repertoire", label: "Repertoire" },
  { id: "games", label: "Games" },
  { id: "studies", label: "Studies" },
  { id: "settings", label: "Settings" },
];
const workspaces = WORKSPACES.filter(item => canOpenWorkspace(item.id, independentStudy));
const PHONE_PRIMARY: View[] = ["train", "tactics", "endgames"];
export default function Navbar({
  view,
  setView,
}: {
  view: View;
  setView: (view: View) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  usePopupKeyboard(menuRef, () => {
    setMenuOpen(false);
    [...(navRef.current?.querySelectorAll<HTMLButtonElement>('[aria-expanded="true"]') ?? [])]
      .find(button => button.getClientRects().length > 0)?.focus();
  }, menuOpen);
  const select = (next: View) => {
    setMenuOpen(false);
    setView(next);
    window.scrollTo({ top: 0 });
  };
  const destination = (
    item: (typeof WORKSPACES)[number],
    className?: string,
  ) => (
    <Button
      key={item.id}
      className={className}
      aria-current={view === item.id ? "page" : undefined}
      onPointerEnter={() => { if (!independentStudy) void preloadView(item.id).catch(() => undefined); }}
      onFocus={() => { if (!independentStudy) void preloadView(item.id).catch(() => undefined); }}
      onClick={() => select(item.id)}
    >
      {item.label}
    </Button>
  );
  return (
    <nav
      className="nav"
      aria-label="Primary navigation"
      ref={navRef}
    >
      <div className="desktop-navigation">
        {workspaces.map((item) => destination(item))}
      </div>
      <div className="phone-navigation">
        {workspaces.filter((item) => PHONE_PRIMARY.includes(item.id)).map(
          (item) => destination(item),
        )}
        <Button
          aria-expanded={menuOpen}
          aria-controls="workspace-menu"
          aria-current={!PHONE_PRIMARY.includes(view) ? "page" : undefined}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          More
        </Button>
      </div>
      <Button
        className="tablet-navigation"
        aria-expanded={menuOpen}
        aria-controls="workspace-menu"
        onClick={() => setMenuOpen(!menuOpen)}
      >
        {workspaces.find((item) => item.id === view)?.label ?? "Train"} ▾
      </Button>
      {menuOpen && (
        <div
          ref={menuRef}
          id="workspace-menu"
          className="workspace-menu"
          aria-label="All sections"
        >
          {workspaces.map((item) =>
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
