type CleanupOperation = () => unknown | Promise<unknown>;

interface RedisAdmissionOperations {
  stopRedis: CleanupOperation;
  pauseRedis: CleanupOperation;
  startRedis: CleanupOperation;
  unpauseRedis: CleanupOperation;
  closePage: CleanupOperation;
  cleanupFixture: CleanupOperation;
}

// Test-specific ownership of the Redis admission proof's resources.
export class RedisAdmissionCleanup {
  private redisRestartRequired = false;
  private redisUnpauseRequired = false;
  private readonly listeners = new Set<CleanupOperation>();
  private readonly cleanupFailures: Error[] = [];

  constructor(private readonly operations: RedisAdmissionOperations) {}

  async stopRedis() {
    this.redisRestartRequired = true;
    await this.operations.stopRedis();
  }

  async pauseRedis() {
    this.redisUnpauseRequired = true;
    await this.operations.pauseRedis();
  }

  async restoreRedis() {
    if (this.redisUnpauseRequired) {
      await this.operations.unpauseRedis();
      this.redisUnpauseRequired = false;
    }
    if (this.redisRestartRequired) {
      await this.operations.startRedis();
      this.redisRestartRequired = false;
    }
  }

  async withResponseListener(removeListener: CleanupOperation, scenario: () => Promise<void>) {
    this.listeners.add(removeListener);
    try {
      await scenario();
    } finally {
      await this.removeListener(removeListener);
    }
  }

  private async attempt(resource: string, operation: CleanupOperation) {
    try {
      await operation();
    } catch (failure) {
      this.cleanupFailures.push(new Error(`Redis admission cleanup failed: ${resource}`, { cause: failure }));
    }
  }

  private async removeListener(removeListener: CleanupOperation) {
    await this.attempt("response listener", async () => {
      await removeListener();
      this.listeners.delete(removeListener);
    });
  }

  async run(body: () => Promise<void>) {
    let bodyFailed = false;
    let originalFailure: unknown;
    try {
      await body();
    } catch (failure) {
      bodyFailed = true;
      originalFailure = failure;
    } finally {
      for (const removeListener of this.listeners) await this.removeListener(removeListener);
      if (this.redisUnpauseRequired) await this.attempt("Redis unpause", async () => {
        await this.operations.unpauseRedis();
        this.redisUnpauseRequired = false;
      });
      if (this.redisRestartRequired) await this.attempt("Redis restart", async () => {
        await this.operations.startRedis();
        this.redisRestartRequired = false;
      });
      await this.attempt("Playwright page", this.operations.closePage);
      await this.attempt("PostgreSQL fixture", this.operations.cleanupFixture);
    }
    if (this.cleanupFailures.length) {
      throw new AggregateError(
        bodyFailed ? [originalFailure, ...this.cleanupFailures] : this.cleanupFailures,
        bodyFailed ? "Redis admission test failed; resource cleanup also failed" : "Redis admission resource cleanup failed",
        bodyFailed ? { cause: originalFailure } : undefined,
      );
    }
    if (bodyFailed) throw originalFailure;
  }
}
