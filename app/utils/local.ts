// Returns true if the application is running with a local API server, false otherwise.
export function usesLocalApi(): boolean {
  return (
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1"].includes(location.hostname)
  );
}

// Returns a string representing the local day key in the format "YYYY-MM-DD".
export function localDayKey(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
