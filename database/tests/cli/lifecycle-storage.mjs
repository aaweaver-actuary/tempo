// Fixed disposable PostgreSQL CLI proof operations; lifecycle/guards stay with the owning runner.
export function markHistoryIncorrect(compose) {
  return compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
          "UPDATE reviews SET rating='incorrect' WHERE card_id='cli-history'"]);
}

export function markHistoryCorrect(compose) {
  return compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c", "UPDATE reviews SET rating='correct' WHERE card_id='cli-history'"]);
}

export function readLatestMigration(compose) {
  return compose(["exec", "-T", "postgres", "psql", "-tA", "-U", "postgres", "-d", "tempo", "-c", "SELECT MAX(version) FROM tempo_schema_migrations"]);
}

export function forgetMigrationSuffix(runtime) {
  return runtime.compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
      `DELETE FROM tempo_schema_migrations WHERE version>=40`]);
}
