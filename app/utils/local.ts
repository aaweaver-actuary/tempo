declare const __TEMPO_DEMO__: boolean;

// Static builds deliberately have no authoritative API. Configured Docker APIs
// also work when the web application is accessed through a LAN hostname.
export function usesLocalApi(): boolean {
  if (typeof __TEMPO_DEMO__ !== "undefined") return !__TEMPO_DEMO__;
  const configured = typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL;
  return (
    typeof window !== "undefined" &&
    Boolean(configured || ["localhost", "127.0.0.1"].includes(location.hostname))
  );
}

// Returns a string representing the local day key in the format "YYYY-MM-DD".
export function localDayKey(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
