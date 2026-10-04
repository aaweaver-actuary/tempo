import { useSyncExternalStore } from "react";

const phoneMediaQuery = "(max-width: 767px)";
function subscribeToPhoneViewport(onChange: () => void) {
  const mediaQuery = window.matchMedia?.(phoneMediaQuery);
  mediaQuery?.addEventListener("change", onChange);
  return () => mediaQuery?.removeEventListener("change", onChange);
}

export function usePhoneViewport() {
  return useSyncExternalStore(subscribeToPhoneViewport,
    () => window.matchMedia?.(phoneMediaQuery).matches ?? false,
    () => false);
}
