import { backgroundDiagnosticsSchema, type BackgroundDiagnostics } from "../domain/schemas/background-diagnostics";

export type ActivityClassification = "progressing" | "needs_attention" | "waiting" | "disabled" | "paused" | "finished" | "history";

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
  paused_by_settings?: boolean;
  promoted: boolean;
  classification?: ActivityClassification;
  logical_id?: string;
  stages?: ActivityItem[];
  stage_count?: number;
  waiting_reason?: string | null;
  completed_at?: string | null;
  last_progress_at?: string | null;
  health?: string;
};

export type ActivityResponse = {
  items: ActivityItem[];
  counts: { running: number; queued: number; paused: number; failed: number; disabled?: number; manual_paused?: number; finished?: number; history?: number };
  total: number;
  next_offset: number | null;
  clearable_finished?: number;
  completion_cutoff?: string | null;
  completion_snapshot?: string | null;
  cleared_through?: string | null;
  generated_at?: string;
  available?: boolean;
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
