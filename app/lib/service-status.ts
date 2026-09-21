export type ServiceTask = {
  id: string;
  kind: string;
  state: "queued" | "leased" | "retrying" | "complete" | "failed" | "superseded";
  phase: string;
  attempts: number;
  max_attempts: number;
  next_retry: string;
  created_at: string;
  updated_at: string;
  age_seconds: number;
  last_error: string | null;
};

export type ServiceStatus = {
  tasks: ServiceTask[];
  counts: {
    queued: number;
    active: number;
    failed: number;
    oldest_queued_age_seconds: number;
  };
  writer: { healthy: boolean; foreground: number; background: number };
  queue_projections: Array<{
    queue_date: string;
    state: "ready" | "refreshing" | "failed";
    generation: number;
    updated_at: string | null;
    refresh_pending: number;
    last_error: string | null;
    blocked_count?: number;
  }>;
};

let latestStatus: ServiceStatus | null = null;

export function setLatestServiceStatus(status: ServiceStatus) {
  latestStatus = status;
}

export function serviceStatusSnapshot() {
  return latestStatus;
}
