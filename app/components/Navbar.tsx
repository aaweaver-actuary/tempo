import { View } from "@/app/types";
import { preloadView } from "../lib/workspace-data";

function getViewList(): View[] {
  return [
    "train",
    "tactics",
    "endgames",
    "repertoire",
    "builder",
    "games",
    "progress",
    "settings",
  ];
}

interface NavbarProps {
  view: View;
  setView: (view: View) => void;
}

export default function Navbar({ view, setView }: NavbarProps) {
  function isViewActive(item: View): boolean {
    return view === item;
  }

  function selectViewClass(item: View): string {
    return isViewActive(item) ? "active" : "";
  }

  return (
    <nav className="nav" aria-label="Primary navigation">
      {getViewList().map((item) => (
        <button
          className={selectViewClass(item)}
          key={item}
          onPointerEnter={() => void preloadView(item).catch(() => undefined)}
          onFocus={() => void preloadView(item).catch(() => undefined)}
          onClick={() => setView(item)}
        >
          {item[0].toUpperCase() + item.slice(1)}
        </button>
      ))}
    </nav>
  );
}
