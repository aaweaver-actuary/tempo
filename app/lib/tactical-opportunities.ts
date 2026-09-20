export function formatConversionRate(exploited: number, opportunities: number): string {
  if (opportunities <= 0) return "—";
  return `${Math.round((exploited / opportunities) * 1000) / 10}%`;
}

export function tacticalQueueBoardOrientation(color: "white" | "black"): "white" | "black" {
  return color === "black" ? "black" : "white";
}
