// @vitest-environment node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";

const repositoryRoot = resolve(process.cwd());
const composeFile = join(repositoryRoot, "docker-compose.study.yml");
const invocationId = "0123456789abcdef";
const projectName = `tempo-study-test-${invocationId}`;

type ComposeService = {
  build?: { context?: string; dockerfile?: string };
  command?: string | string[] | null;
  container_name?: string;
  depends_on?: Record<string, { condition?: string }>;
  entrypoint?: string | string[] | null;
  environment?: Record<string, string>;
  image?: string;
  ports?: Array<{
    host_ip?: string;
    mode?: string;
    protocol?: string;
    published?: string;
    target?: number;
  }>;
  profiles?: string[];
  read_only?: boolean;
  restart?: string;
  secrets?: Array<string | { source?: string; target?: string }>;
  user?: string;
  labels?: Record<string, string>;
  volumes?: Array<{
    read_only?: boolean;
    source?: string;
    target?: string;
    type?: string;
  }>;
};

type ComposeModel = {
  name: string;
  services: Record<string, ComposeService>;
  volumes: Record<string, { external?: boolean; labels?: Record<string, string>; name?: string }>;
  networks: Record<string, { external?: boolean; labels?: Record<string, string>; name?: string }>;
};

type ConfigFixture = {
  directory: string;
  values: Record<string, string>;
};

function createConfigFixture(): ConfigFixture {
  const directory = mkdtempSync(join(tmpdir(), "tempo-study-compose-"));
  const invocationDirectory = join(directory, invocationId);
  mkdirSync(invocationDirectory, { mode: 0o700 });
  chmodSync(invocationDirectory, 0o700);

  const adminUrlFile = join(directory, "admin-url");
  const postgresPasswordFile = join(directory, "postgres-password");
  writeFileSync(adminUrlFile, "postgresql://postgres:test-only@postgres:5432/postgres\n", { mode: 0o600 });
  writeFileSync(postgresPasswordFile, "test-only-password\n", { mode: 0o600 });

  const values = {
    TEMPO_STUDY_RUN_ID: invocationId,
    TEMPO_STUDY_WORKSPACE: "study-compose-contract",
    TEMPO_STUDY_TIMEZONE: "America/New_York",
    TEMPO_STUDY_PORT: "4187",
    TEMPO_STUDY_ADMIN_URL_FILE: adminUrlFile,
    TEMPO_STUDY_POSTGRES_PASSWORD_FILE: postgresPasswordFile,
    TEMPO_STUDY_INVOCATION_DIRECTORY: invocationDirectory,
  };
  // A conflicting ambient file proves the command uses only --env-file.
  writeFileSync(join(directory, ".env"), [
    "TEMPO_STUDY_RUN_ID=ffffffffffffffff",
    "TEMPO_STUDY_WORKSPACE=ambient-workspace",
    "TEMPO_STUDY_TIMEZONE=UTC",
    "TEMPO_STUDY_PORT=4199",
    `TEMPO_STUDY_ADMIN_URL_FILE=${adminUrlFile}`,
    `TEMPO_STUDY_POSTGRES_PASSWORD_FILE=${postgresPasswordFile}`,
    `TEMPO_STUDY_INVOCATION_DIRECTORY=${invocationDirectory}`,
    "",
  ].join("\n"));

  return { directory, values };
}

function composeConfig(fixture: ConfigFixture, overrides: Record<string, string | undefined> = {}) {
  const values = { ...fixture.values, ...overrides };
  const envFile = join(fixture.directory, `compose-${Object.keys(overrides).join("-") || "complete"}.env`);
  const content = Object.entries(values)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string")
    .map(([key, value]) => `${key}=${value}`)
    .join("\n") + "\n";
  writeFileSync(envFile, content, { mode: 0o600 });

  return spawnSync("docker", [
    "compose",
    "--profile", "bootstrap",
    "--env-file", envFile,
    "--project-directory", repositoryRoot,
    "-f", composeFile,
    "-p", projectName,
    "config", "--format", "json",
  ], {
    cwd: fixture.directory,
    encoding: "utf8",
    env: {
      HOME: homedir(),
      PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
      TMPDIR: fixture.directory,
    },
    maxBuffer: 2 * 1024 * 1024,
  });
}

it("renders only the isolated Study services with explicit inputs", () => {
  const fixture = createConfigFixture();
  try {
    const result = composeConfig(fixture);
    expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
    const model = JSON.parse(result.stdout) as ComposeModel;
    expect(model.name).toBe(projectName);
    expect(Object.keys(model.services).sort()).toEqual(["bootstrap", "learning", "postgres", "study-web"]);
    expect(Object.values(model.services).every((service) => service.container_name === undefined)).toBe(true);
    expect(Object.values(model.volumes).every((volume) => volume.external !== true)).toBe(true);
    expect(Object.keys(model.networks)).toEqual(["default"]);
    expect(Object.values(model.networks).every((network) => network.external !== true)).toBe(true);
    expect(model.volumes["postgres-data"]?.name).toBe(`${projectName}_postgres-data`);
    expect(model.volumes["runtime-secrets"]?.name).toBe(`${projectName}_runtime-secrets`);
    expect(model.networks.default?.name).toBe(`${projectName}_default`);
    expect(model.networks.default?.external).not.toBe(true);
    expect(model.networks.default?.labels).toMatchObject({
      "tempo.owner": projectName,
      "tempo.disposable": "true",
    });

    const { bootstrap, learning, postgres, "study-web": studyWeb } = model.services;
    expect(postgres.image).toBe("postgres:18.6-trixie");
    expect(postgres.command).toEqual(["postgres", "-c", "max_locks_per_transaction=512"]);
    expect(postgres.labels).toMatchObject({ "tempo.owner": projectName, "tempo.disposable": "true" });
    expect(postgres.ports ?? []).toEqual([]);
    for (const service of [bootstrap, learning, studyWeb]) {
      expect(service.labels ?? {}).toEqual({});
    }
    expect(model.volumes["postgres-data"]?.labels).toMatchObject({
      "tempo.owner": projectName,
      "tempo.disposable": "true",
    });
    expect(postgres.secrets?.map((secret) => typeof secret === "string" ? secret : secret.source))
      .toEqual(["study-postgres-password"]);

    expect(learning.image).toBe(`tempo-study-learning:${invocationId}`);
    expect(learning.build?.dockerfile).toBe("services/learning/runtime/Dockerfile");
    expect(learning.user).toBe("10001:10001");
    expect(learning.entrypoint ?? null).toBeNull();
    expect(learning.command ?? null).toBeNull();
    expect(learning.ports ?? []).toEqual([]);
    expect(learning.environment).toMatchObject({
      TEMPO_LEARNING_WORKSPACE: fixture.values.TEMPO_STUDY_WORKSPACE,
      TEMPO_LEARNING_DATABASE_URL: `host=postgres port=5432 dbname=learning_study_test_${invocationId} user=learning_study_test_${invocationId}_writer`,
      TEMPO_LEARNING_READER_URL: `host=postgres port=5432 dbname=learning_study_test_${invocationId} user=learning_study_test_${invocationId}_reader`,
      TEMPO_LEARNING_TOKEN_FILE: `/run/study-runtime/${invocationId}/bootstrap/study.token`,
      TEMPO_LEARNING_OPERATOR_TOKEN_FILE: `/run/study-runtime/${invocationId}/bootstrap/operator.token`,
      TEMPO_LEARNING_ORIGINS: "http://127.0.0.1:4187",
      TEMPO_LEARNING_HOSTS: "127.0.0.1:4187",
      TEMPO_LEARNING_TIMEZONE: "America/New_York",
      TEMPO_LEARNING_MAINTENANCE: "1",
      PGPASSFILE: `/run/study-runtime/${invocationId}/bootstrap/runtime.pgpass`,
    });
    expect(learning.depends_on?.bootstrap?.condition).toBe("service_completed_successfully");
    expect(learning.depends_on?.postgres?.condition).toBe("service_healthy");

    expect(bootstrap.profiles).toEqual(["bootstrap"]);
    expect(bootstrap.image).toBe(learning.image);
    expect(bootstrap.user).toBe("0:0");
    expect(bootstrap.entrypoint).toEqual(["/bin/sh", "-ec"]);
    expect(bootstrap.read_only).toBe(true);
    expect(bootstrap.restart).toBe("no");
    expect(bootstrap.depends_on?.postgres?.condition).toBe("service_healthy");
    const bootstrapCommand = Array.isArray(bootstrap.command) ? bootstrap.command.join("\n") : bootstrap.command ?? "";
    expect(bootstrapCommand).toContain("/opt/learning/bin/python -I");
    expect(bootstrapCommand).toContain("--ownership-receipt-file");
    expect(bootstrapCommand).toContain("--admin-url-file");
    expect(bootstrapCommand).toContain("--output-directory");
    expect(bootstrapCommand).toContain("chown -R 10001:10001");
    expect(bootstrapCommand).not.toMatch(/\bdocker\b/);
    expect(bootstrap.secrets?.map((secret) => typeof secret === "string" ? secret : secret.source))
      .toEqual(["study-admin-url"]);

    const runtimeVolume = bootstrap.volumes?.find((volume) => volume.target === "/runtime");
    expect(runtimeVolume?.source).toBe("runtime-secrets");
    expect(bootstrap.volumes?.find((volume) => volume.target === `/run/study-ownership/${invocationId}`))
      .toMatchObject({
        type: "bind",
        source: fixture.values.TEMPO_STUDY_INVOCATION_DIRECTORY,
        read_only: true,
      });
    expect(bootstrap.volumes?.find((volume) => volume.target === "/source/database/tempo_learning_database"))
      .toMatchObject({ type: "bind", read_only: true });
    expect(learning.volumes?.find((volume) => volume.target === "/run/study-runtime"))
      .toMatchObject({ source: "runtime-secrets", read_only: true });
    for (const service of [learning, studyWeb]) {
      expect(service.secrets ?? []).toEqual([]);
      expect(JSON.stringify(service)).not.toContain("study-admin-url");
      expect(JSON.stringify(service)).not.toContain("study-ownership");
      expect(JSON.stringify(service)).not.toContain("study-postgres-password");
    }

    expect(studyWeb.image).toBe(`tempo-study-web:${invocationId}`);
    expect(studyWeb.build?.dockerfile).toBe("Dockerfile.study-web");
    expect(studyWeb.ports).toHaveLength(1);
    expect(studyWeb.ports?.[0]).toMatchObject({
      host_ip: "127.0.0.1",
      published: "4187",
      target: 80,
    });
    if (studyWeb.ports?.[0]?.protocol !== undefined) {
      expect(studyWeb.ports[0].protocol).toBe("tcp");
    }
    expect(studyWeb.depends_on?.learning?.condition).toBe("service_healthy");

    const nginx = readFileSync(join(repositoryRoot, "nginx.study.conf"), "utf8");
    expect(nginx).toContain("proxy_pass http://learning:8001;");
    expect(nginx).toContain("location ^~ /api/ {");
    expect(nginx).toContain("location ^~ /health/ {");
    expect([...nginx.matchAll(/proxy_pass http:\/\/learning:8001;/g)]).toHaveLength(2);
    expect(nginx).toContain("proxy_set_header Host $http_host;");
    expect(nginx).toContain("proxy_set_header Origin $http_origin;");
    expect(nginx).toContain("proxy_set_header Authorization $http_authorization;");
    expect(nginx).toContain("proxy_set_header X-Tempo-Workspace $http_x_tempo_workspace;");

    const runtimeDockerfile = readFileSync(join(repositoryRoot, "services/learning/runtime/Dockerfile"), "utf8");
    expect(runtimeDockerfile).toContain('ENTRYPOINT ["/opt/learning/bin/python", "-I", "-m", "tempo_learning_runtime.cli"]');
    expect(runtimeDockerfile).toContain('CMD ["serve", "--host", "0.0.0.0", "--port", "8001"]');
  } finally {
    rmSync(fixture.directory, { recursive: true, force: true });
  }
});

it("rejects missing required Study configuration instead of discovering ambient .env", () => {
  const requiredInputs = [
    "TEMPO_STUDY_RUN_ID",
    "TEMPO_STUDY_WORKSPACE",
    "TEMPO_STUDY_TIMEZONE",
    "TEMPO_STUDY_PORT",
    "TEMPO_STUDY_ADMIN_URL_FILE",
    "TEMPO_STUDY_POSTGRES_PASSWORD_FILE",
    "TEMPO_STUDY_INVOCATION_DIRECTORY",
  ];

  for (const input of requiredInputs) {
    const fixture = createConfigFixture();
    try {
      const result = composeConfig(fixture, { [input]: undefined });
      expect(result.status, `${input} unexpectedly accepted\n${result.stdout}\n${result.stderr}`).not.toBe(0);
      expect(result.stderr).toContain(input);
    } finally {
      rmSync(fixture.directory, { recursive: true, force: true });
    }
  }
});
