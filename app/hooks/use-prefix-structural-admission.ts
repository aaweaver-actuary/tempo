import { useEffect, useState } from "react";
import { readPrefixStructuralAdmission, subscribePrefixTransitions, type StructuralMutationScope } from "../lib/prefix-transition-command";

/** Rendering is advisory; command admission always reads again under the journal lock. */
export function usePrefixStructuralAdmission(scope: StructuralMutationScope, enabled = true) {
  const repertoireIds = JSON.stringify(scope.repertoireIds);
  const cardIds = JSON.stringify(scope.cardIds ?? []);
  const scopeComplete = scope.scopeComplete;
  const inspect = () => {
    if (!enabled) return { paused: false, reason: undefined as string | undefined };
    try { return readPrefixStructuralAdmission(scope); }
    catch (failure) { return { paused: true, reason: failure instanceof Error ? failure.message : "Prefix recovery data is unavailable. Restore browser access before editing." }; }
  };
  const [admission, setAdmission] = useState(inspect);
  useEffect(() => {
    let disposed = false;
    const update = () => {
      if (disposed) return;
      if (!enabled) { setAdmission({ paused: false, reason: undefined }); return; }
      try { setAdmission(readPrefixStructuralAdmission({ repertoireIds: JSON.parse(repertoireIds), cardIds: JSON.parse(cardIds), scopeComplete })); }
      catch (failure) { setAdmission({ paused: true, reason: failure instanceof Error ? failure.message : "Prefix recovery data is unavailable. Restore browser access before editing." }); }
    };
    queueMicrotask(update);
    const unsubscribe = subscribePrefixTransitions(update);
    return () => { disposed = true; unsubscribe(); };
  }, [repertoireIds, cardIds, scopeComplete, enabled]);
  return admission;
}
