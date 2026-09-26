import { useState, useEffect } from "react";
import { JSX } from "react/jsx-runtime";


export function OutcomeFlash({ outcome }: { outcome: "correct" | "wrong"; }): JSX.Element | null {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(false), 1000);
    return () => window.clearTimeout(timer);
  }, []);
  if (!visible) return null;
  return (
    <div
      className={`outcome-flash ${outcome}`}
      role="status"
      aria-live="assertive"
    >
      {outcome === "correct" ? "✓" : "×"}
    </div>
  );
}
