import { backgroundDiagnosticsSchema, type BackgroundDiagnostics } from "../domain/schemas/background-diagnostics";

export type ActivityItem = {
  source: string;
  id: string;
  title: string;
  state: string;
  phase: string;
  completed: number | null;
  total: number | null;
  updated_at: string;
  error: string | null;
  paused: boolean;
  promoted: boolean;
};

export type ActivityResponse = {
  items: ActivityItem[];
  counts: { running: number; queued: number; paused: number; failed: number };
  total: number;
  next_offset: number | null;
  writer?: { healthy: boolean; foreground: number; background: number };
};

let latestStatus: ActivityResponse | null = null;

export function setLatestServiceStatus(status: ActivityResponse) {
  latestStatus = status;
}

export function serviceStatusSnapshot() {
  return latestStatus;
}

let latestBackgroundDiagnostics: BackgroundDiagnostics | null = null;

export function setBackgroundDiagnostics(value: unknown) {
  latestBackgroundDiagnostics = value === null ? null : backgroundDiagnosticsSchema.parse(value);
}

export function backgroundDiagnosticsSnapshot() {
  return latestBackgroundDiagnostics;
}
