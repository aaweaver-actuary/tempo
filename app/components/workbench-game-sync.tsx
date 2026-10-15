import type { ReactNode } from "react";
import { useGameSync } from "../hooks/use-game-sync";

/** Optional provider work mounts only with its Workbench consumer. */
export function WorkbenchGameSync({ children }: { children: (sync: ReturnType<typeof useGameSync>) => ReactNode }) {
  const sync = useGameSync();
  return children(sync);
}
