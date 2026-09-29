const unsafeTestEnvironmentKeys = new Set([
  "TEMPO_DATABASE_READ_URL",
  "TEMPO_DATABASE_WRITE_URL",
  "TEMPO_DB_PATH",
  "TEMPO_MIGRATION_DATABASE_URL",
  "TEMPO_POSTGRES_ADMIN_URL",
  "TEMPO_REDIS_URL",
  "TEMPO_ENGINE_OUTBOX_PATH",
  "TEMPO_TEST_INSTANCE",
  "TEMPO_TEST_DATA",
  "TEMPO_TEST_PORT",
  "TEMPO_PG_TEST_SECRETS",
  "TEMPO_PG_TEST_PORT",
  "TEMPO_POSTGRES_ADMIN_PASSWORD_FILE",
  "TEMPO_POSTGRES_READER_PASSWORD_FILE",
  "TEMPO_POSTGRES_WRITER_PASSWORD_FILE",
  "TEMPO_POSTGRES_ADMIN_PGPASS_FILE",
  "TEMPO_POSTGRES_READER_PGPASS_FILE",
  "TEMPO_POSTGRES_WRITER_PGPASS_FILE",
  "TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS",
  "TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS",
  "TEMPO_BROWSER_API_PORT",
  "TEMPO_BROWSER_UI_PORT",
  "TEMPO_DOCKER_URL",
  "TEMPO_TEST_DOCKER_URL",
  "TEMPO_PG_BROWSER_GREP",
  "TEMPO_PG_BROWSER_FILE",
  "COMPOSE_FILE",
  "COMPOSE_PROJECT_NAME",
  "DATABASE_URL",
  "REDIS_URL",
  "CELERY_BROKER_URL",
  "BROKER_URL",
  "PGHOST",
  "PGPORT",
  "PGDATABASE",
  "PGUSER",
  "PGPASSWORD",
  "PGPASSFILE",
  "PGSERVICE",
  "PGSERVICEFILE",
]);

export function createIsolatedTestEnvironment(sourceEnvironment, overrides = {}) {
  const isolatedEnvironment = { ...sourceEnvironment };
  for (const key of Object.keys(isolatedEnvironment)) {
    if (unsafeTestEnvironmentKeys.has(key)
      || key.startsWith("TEMPO_POSTGRES_")
      || key.startsWith("TEMPO_PG_TEST_")) delete isolatedEnvironment[key];
  }
  return { ...isolatedEnvironment, ...overrides };
}

export function hasUnsafeTestEnvironment(environment) {
  return Object.keys(environment).some((key) => unsafeTestEnvironmentKeys.has(key)
    || key.startsWith("TEMPO_POSTGRES_") || key.startsWith("TEMPO_PG_TEST_"));
}
