import { useEffect, useState } from "react";

export function MoveNavigator({ cursor, length, onChange }: { cursor: number; length: number; onChange: (cursor: number) => void }) {
  return (
    <div className="move-navigator" aria-label="Move navigation">
      <button onClick={() => onChange(0)} disabled={cursor === 0} aria-label="Go to start">↑</button>
      <button onClick={() => onChange(Math.max(0, cursor - 1))} disabled={cursor === 0} aria-label="Previous move">←</button>
      <span>{cursor} / {length}</span>
      <button onClick={() => onChange(Math.min(length, cursor + 1))} disabled={cursor === length} aria-label="Next move">→</button>
      <button onClick={() => onChange(length)} disabled={cursor === length} aria-label="Go to end">↓</button>
    </div>
  );
}

export function OutcomeFlash({ outcome }: { outcome: 'correct' | 'wrong' }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(false), 1000);
    return () => window.clearTimeout(timer);
  }, []);
  if (!visible) return null;
  return <div className={`outcome-flash ${outcome}`} role="status" aria-live="assertive">{outcome === 'correct' ? '✓' : '×'}</div>;
}
