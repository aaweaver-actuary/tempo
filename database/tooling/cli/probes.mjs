// Fixed native database probes; CLI classification and timing policy stay with callers.
export function readMigrationLedger(runtime) {
  return runtime.compose(["exec", "-T", "-e",
    "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=100", "postgres", "psql", "-X", "-tA", "-v", "ON_ERROR_STOP=1", "-U",
    runtime.configuration.services.postgres.environment.POSTGRES_USER ?? "postgres", "-d", runtime.configuration.services.postgres.environment.POSTGRES_DB,
    "-c", "SELECT version FROM tempo_schema_migrations ORDER BY version"], { allowFailure: true, timeout: 5000 });
}

export function probeDiagnosticRedis(runtime) {
  return runtime.compose(["exec", "-T", "redis", "redis-cli", "-e", "--raw", "ping"], { allowFailure: true, timeout: 5000 });
}

export function probeLifecycleRedis(compose, options) {
  return compose(["exec", "-T", "redis", "redis-cli", "-e", "--raw", "ping"], options);
}
