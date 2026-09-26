import type { ReactNode } from "react";
import { Button } from "./buttons/BaseButton";


export function Notice({
  children, onRetry, error = false,
}: { children: ReactNode; onRetry?: () => void; error?: boolean; }): import("react/jsx-runtime").JSX.Element {
  return (
    <div className={`ui-notice${error ? " error" : ""}`} role={error ? "alert" : "status"}>
      <span>{children}</span>
      {onRetry && <Button onClick={onRetry}>Retry</Button>}
    </div>
  );
}
