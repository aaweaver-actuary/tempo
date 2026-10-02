/** One component-owned queue for Discoveries recommendation requests only. */
export type DiscoveryPreviewPriority = "explicit" | "look-ahead" | "safe-break" | "background";
export type DiscoveryPreviewWork = {
  identity: string;
  discoveryId: string;
  priority: DiscoveryPreviewPriority;
  feedOrder: number;
};
export type DiscoveryPreviewDiagnostics = {
  enqueued: number; started: number; completed: number; active: number; maximumActive: number;
  queued: number; staleQueuedDiscarded: number; staleResultsIgnored: number; retriesScheduled: number;
  validationInvocations: number; validationCacheHits: number;
};
const priorityOrder: Record<DiscoveryPreviewPriority, number> = {
  explicit: 0, "look-ahead": 1, "safe-break": 2, background: 3,
};

export class DiscoveryPreviewScheduler {
  private currentIdentities = new Set<string>();
  private demand = new Map<string, DiscoveryPreviewWork>();
  private pending = new Map<string, DiscoveryPreviewWork>();
  private active = new Map<string, AbortController>();
  private retryAt = new Map<string, number>();
  private finished = new Set<string>();
  private wakeTimer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;
  private diagnostics: DiscoveryPreviewDiagnostics = { enqueued: 0, started: 0, completed: 0,
    active: 0, maximumActive: 0, queued: 0, staleQueuedDiscarded: 0, staleResultsIgnored: 0,
    retriesScheduled: 0, validationInvocations: 0, validationCacheHits: 0 };

  constructor(private readonly run: (work: DiscoveryPreviewWork, context: {
    signal: AbortSignal; isCurrent: () => boolean;
  }) => Promise<"complete" | "retry">, readonly concurrency = 2,
    private readonly now = () => performance.now(), private readonly retryDelayMs = 30_000,
    private readonly canStart: (work: DiscoveryPreviewWork) => boolean = () => true) {
    if (!Number.isInteger(concurrency) || concurrency < 1) throw new Error("Preview concurrency must be a positive integer");
  }

  update(currentIdentities: Iterable<string>, demand: DiscoveryPreviewWork[]) {
    if (this.disposed) return;
    this.currentIdentities = new Set(currentIdentities);
    this.demand = new Map(demand.filter(work => this.currentIdentities.has(work.identity))
      .map(work => [work.identity, work]));
    for (const identity of this.pending.keys()) {
      if (!this.currentIdentities.has(identity)) {
        this.pending.delete(identity); this.diagnostics.staleQueuedDiscarded++;
      } else if (!this.demand.has(identity)) this.pending.delete(identity);
    }
    for (const identity of this.finished) if (!this.currentIdentities.has(identity)) this.finished.delete(identity);
    for (const identity of this.retryAt.keys()) if (!this.currentIdentities.has(identity)) this.retryAt.delete(identity);
    // Obsolete requests retain their slot until HTTP/body processing settles. We
    // fence publication rather than treating an abort as server cancellation.
    for (const work of this.demand.values()) {
      if (this.active.has(work.identity) || this.finished.has(work.identity)) continue;
      if (!this.pending.has(work.identity)) this.diagnostics.enqueued++;
      this.pending.set(work.identity, work);
    }
    this.drain();
  }

  private drain() {
    if (this.disposed) return;
    if (this.wakeTimer !== undefined) clearTimeout(this.wakeTimer);
    this.wakeTimer = undefined;
    const due = [...this.pending.values()].filter(work => this.canStart(work) && (this.retryAt.get(work.identity) ?? 0) <= this.now())
      .sort((left, right) => priorityOrder[left.priority] - priorityOrder[right.priority] ||
        left.feedOrder - right.feedOrder || left.identity.localeCompare(right.identity));
    while (this.active.size < this.concurrency && due.length) {
      const work = due.shift()!;
      this.pending.delete(work.identity);
      const controller = new AbortController();
      this.active.set(work.identity, controller);
      this.diagnostics.started++;
      this.diagnostics.maximumActive = Math.max(this.diagnostics.maximumActive, this.active.size);
      const isCurrent = () => !this.disposed && this.currentIdentities.has(work.identity);
      void Promise.resolve().then(() => this.run(work, { signal: controller.signal, isCurrent }))
        .then(outcome => {
          if (!isCurrent()) { this.diagnostics.staleResultsIgnored++; return; }
          if (outcome === "retry") {
            this.retryAt.set(work.identity, this.now() + this.retryDelayMs);
            this.diagnostics.retriesScheduled++;
            const currentDemand = this.demand.get(work.identity);
            if (currentDemand) { this.pending.set(work.identity, currentDemand); this.diagnostics.enqueued++; }
          } else { this.finished.add(work.identity); this.retryAt.delete(work.identity); }
        }, () => {
          // The loader reports actionable failures; this guard also keeps a
          // rejected injected loader from leaking capacity or an unhandled promise.
          if (!isCurrent()) { this.diagnostics.staleResultsIgnored++; return; }
          this.retryAt.set(work.identity, this.now() + this.retryDelayMs);
          this.diagnostics.retriesScheduled++;
          const currentDemand = this.demand.get(work.identity);
          if (currentDemand) this.pending.set(work.identity, currentDemand);
        }).finally(() => {
          this.active.delete(work.identity); this.diagnostics.completed++; this.drain();
        });
    }
    // Due work waits for capacity, not a zero-delay timer loop. Future retries
    // need only one wake, and paused demand creates no retry timer.
    const futureDeadlines = [...this.pending.values()].filter(work => this.canStart(work))
      .map(work => this.retryAt.get(work.identity) ?? 0)
      .filter(deadline => deadline > this.now());
    if (futureDeadlines.length && this.active.size < this.concurrency)
      this.wakeTimer = setTimeout(() => this.drain(), Math.max(1, Math.min(...futureDeadlines) - this.now()));
  }

  recordValidation(cacheHit: boolean) {
    if (cacheHit) this.diagnostics.validationCacheHits++;
    else this.diagnostics.validationInvocations++;
  }

  snapshot(): DiscoveryPreviewDiagnostics {
    return { ...this.diagnostics, active: this.active.size, queued: this.pending.size };
  }

  dispose() {
    this.disposed = true;
    if (this.wakeTimer !== undefined) clearTimeout(this.wakeTimer);
    this.wakeTimer = undefined;
    this.pending.clear(); this.demand.clear(); this.retryAt.clear(); this.finished.clear();
    this.currentIdentities.clear();
    for (const controller of this.active.values()) controller.abort();
  }
}

/** At most one result per identity, with an independent hard memory bound. */
export class DiscoveryPreviewValidationCache {
  private entries = new Map<string, { decision: string; preview: object; matches: boolean }>();
  constructor(private readonly record: (cacheHit: boolean) => void, private readonly limit = 256) {}
  matches(identity: string, decision: string, preview: object, validate: () => boolean): boolean {
    const saved = this.entries.get(identity);
    if (saved?.decision === decision && saved.preview === preview) {
      this.record(true); return saved.matches;
    }
    this.record(false);
    const matches = validate();
    this.entries.delete(identity);
    this.entries.set(identity, { decision, preview, matches });
    if (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value!);
    return matches;
  }
  retain(identities: Set<string>) {
    for (const identity of this.entries.keys()) if (!identities.has(identity)) this.entries.delete(identity);
  }
  clear() { this.entries.clear(); }
  get size() { return this.entries.size; }
}
