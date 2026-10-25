// Actual public Study leaves, injected transport and disposable protocol files only.
// No installed system, database, graph acceptance or user approval is observed.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { targetKey } from "../../../scripts/tempo-deployment.mjs";

// Canonical source-only serving protocol fixture, normalized to fictional paths.
const servingProtocol = {
  "registration": {
    "version": 2,
    "root": "/fixture/release",
    "context": "fixture-context",
    "daemonId": "fixture-daemon",
    "project": "fixture-study",
    "envFile": "/fixture/private/serving.env",
    "composeFiles": [
      "/fixture/release/config/study-persistent/serving.compose.yml"
    ],
    "volumes": {
      "postgres-data": {
        "name": "fixture-data",
        "external": true
      },
      "runtime-secrets": {
        "name": "fixture-secrets",
        "external": true
      }
    },
    "postgresVolumeKey": "postgres-data",
    "postgresMajor": 18,
    "postgresImage": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "webUrl": "http://127.0.0.1:38127",
    "ports": [
      {
        "service": "study-web",
        "host": "127.0.0.1",
        "port": "38127",
        "target": 80
      }
    ],
    "cutover": {
      "mode": "graph-preserving-fresh-start",
      "transitionId": "fixture-transition",
      "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "schema": "sha256:7777777777777777777777777777777777777777777777777777777777777777",
      "images": {
        "postgres": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "learning": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        "study-web": "sha256:3333333333333333333333333333333333333333333333333333333333333333"
      },
      "database": {
        "name": "fixture_learning",
        "volume": "fixture-data"
      },
      "workspace": "fixture-workspace",
      "ingress": {
        "origin": "http://127.0.0.1:38127"
      },
      "composeFiles": [
        "/fixture/release/config/study-persistent/serving.compose.yml"
      ],
      "volumes": {
        "postgres-data": {
          "name": "fixture-data",
          "external": true
        },
        "runtime-secrets": {
          "name": "fixture-secrets",
          "external": true
        }
      }
    },
    "installedStudy": {
      "version": 1,
      "runtime": {
        "version": 1,
        "postgres": {
          "volumeKey": "postgres-data",
          "volumeName": "fixture-data",
          "mountDestination": "/var/lib/postgresql",
          "dataDirectory": "/var/lib/postgresql/18/docker",
          "major": 18,
          "systemIdentifier": "fixture-system-identifier"
        },
        "credentialDigests": {
          "studyTokenSha256": "4444444444444444444444444444444444444444444444444444444444444444",
          "runtimePassfileSha256": "5555555555555555555555555555555555555555555555555555555555555555"
        }
      }
    }
  },
  "configuration": {
    "name": "fixture-study",
    "networks": {
      "persistent": {
        "name": "fixture-network",
        "ipam": {},
        "external": true
      }
    },
    "secrets": {
      "persistent-postgres-password": {
        "name": "fixture-study_persistent-postgres-password",
        "file": "/fixture/private/postgres-password"
      }
    },
    "services": {
      "learning": {
        "restart": "no",
        "command": [
          "serve",
          "--host",
          "0.0.0.0",
          "--port",
          "8001"
        ],
        "entrypoint": null,
        "environment": {
          "PGPASSFILE": "/run/persistent/bootstrap/runtime.pgpass",
          "TEMPO_LEARNING_DATABASE_URL": "host=postgres port=5432 dbname=fixture_learning user=fixture_learning_writer",
          "TEMPO_LEARNING_HOSTS": "127.0.0.1,learning",
          "TEMPO_LEARNING_MAINTENANCE": "1",
          "TEMPO_LEARNING_ORIGINS": "http://127.0.0.1:38127",
          "TEMPO_LEARNING_READER_URL": "host=postgres port=5432 dbname=fixture_learning user=fixture_learning_reader",
          "TEMPO_LEARNING_TIMEZONE": "America/New_York",
          "TEMPO_LEARNING_TOKEN_FILE": "/run/persistent/bootstrap/study.token",
          "TEMPO_LEARNING_WORKSPACE": "fixture-workspace"
        },
        "image": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        "labels": {
          "tempo.disposable": "false",
          "tempo.owner": "fixture-study",
          "tempo.target-kind": "persistent"
        },
        "networks": {
          "persistent": null
        },
        "pull_policy": "never",
        "read_only": true,
        "tmpfs": [
          "/tmp"
        ],
        "user": "10001:10001",
        "volumes": [
          {
            "type": "volume",
            "source": "runtime-secrets",
            "target": "/run/persistent",
            "read_only": true
          }
        ],
        "depends_on": {
          "postgres": {
            "condition": "service_healthy",
            "required": true
          }
        },
        "healthcheck": {
          "test": [
            "CMD",
            "/opt/learning/bin/python",
            "-I",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/ready', timeout=2).close()"
          ],
          "timeout": "4s",
          "interval": "3s",
          "retries": 30
        }
      },
      "postgres": {
        "restart": "no",
        "command": [
          "postgres",
          "-c",
          "max_locks_per_transaction=512"
        ],
        "entrypoint": null,
        "environment": {
          "PGDATA": "/var/lib/postgresql/18/docker",
          "POSTGRES_DB": "postgres",
          "POSTGRES_PASSWORD_FILE": "/run/secrets/persistent-postgres-password",
          "POSTGRES_USER": "postgres"
        },
        "image": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "labels": {
          "tempo.disposable": "false",
          "tempo.owner": "fixture-study",
          "tempo.target-kind": "persistent"
        },
        "networks": {
          "persistent": null
        },
        "pull_policy": "never",
        "secrets": [
          {
            "source": "persistent-postgres-password",
            "target": "persistent-postgres-password",
            "mode": "0400"
          }
        ],
        "volumes": [
          {
            "type": "volume",
            "source": "postgres-data",
            "target": "/var/lib/postgresql",
            "volume": {}
          }
        ],
        "healthcheck": {
          "test": [
            "CMD-SHELL",
            "pg_isready -U postgres -d postgres"
          ],
          "timeout": "2s",
          "interval": "2s",
          "retries": 30
        }
      },
      "study-web": {
        "restart": "no",
        "command": null,
        "entrypoint": null,
        "image": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
        "labels": {
          "tempo.disposable": "false",
          "tempo.owner": "fixture-study",
          "tempo.target-kind": "persistent"
        },
        "networks": {
          "persistent": null
        },
        "pull_policy": "never",
        "depends_on": {
          "learning": {
            "condition": "service_healthy",
            "required": true
          }
        },
        "ports": [
          {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 80,
            "published": "38127",
            "protocol": "tcp"
          }
        ]
      }
    },
    "volumes": {
      "postgres-data": {
        "name": "fixture-data",
        "external": true
      },
      "runtime-secrets": {
        "name": "fixture-secrets",
        "external": true
      }
    }
  },
  "persistentIntent": {
    "kind": "tempo-learning-persistent-target-intent",
    "schema_version": 1,
    "transition_id": "fixture-transition",
    "target_identity": "f1c23daee48f1dd45588c777",
    "context": "fixture-context",
    "daemon_id": "fixture-daemon",
    "project": "fixture-study",
    "source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "images": {
      "postgres": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "learning": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "study-web": "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    },
    "database": "fixture_learning",
    "workspace_id": "fixture-workspace",
    "ingress_origin": "http://127.0.0.1:38127",
    "timezone": "America/New_York",
    "data_volume": "fixture-data",
    "runtime_secrets_volume": "fixture-secrets",
    "network_name": "fixture-network",
    "source_volume_names": [
      "fixture-preserved-source"
    ]
  },
  "persistentReceipt": {
    "kind": "tempo-learning-persistent-bootstrap",
    "schema_version": 1,
    "evidence_scope": "observed",
    "status": "initialized-quarantine-required",
    "intent_sha256": "8888888888888888888888888888888888888888888888888888888888888888",
    "resource_observation_sha256": "9999999999999999999999999999999999999999999999999999999999999999",
    "transition_id": "fixture-transition",
    "target_identity": "f1c23daee48f1dd45588c777",
    "project": "fixture-study",
    "declared_source_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "declared_images": {
      "postgres": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "learning": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "study-web": "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    },
    "observed_postgres_image": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "database": "fixture_learning",
    "workspace_id": "fixture-workspace",
    "ingress_origin": "http://127.0.0.1:38127",
    "timezone": "America/New_York",
    "roles": {
      "owner": "fixture_learning_owner",
      "writer": "fixture_learning_writer",
      "reader": "fixture_learning_reader"
    },
    "endpoint": {
      "host": "postgres",
      "port": 5432
    },
    "schema": {
      "identity": "sha256:7777777777777777777777777777777777777777777777777777777777777777"
    },
    "system_identifier": "fixture-system-identifier",
    "private_output_sha256": {
      "study.token": "4444444444444444444444444444444444444444444444444444444444444444",
      "runtime.pgpass": "5555555555555555555555555555555555555555555555555555555555555555",
      "connection.json": "6666666666666666666666666666666666666666666666666666666666666666",
      "operator.token": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }
};

export async function createStudyStartupFixture(t, { command = "start", beforeCommand } = {}) {
  const [{ createAdmissionProtocolFixture }, { executeFreshStartTransition }] = await Promise.all([
    import("./tempo-study-admission-fixture.mjs"), import("../../../scripts/tempo-cutover-transition.mjs"),
  ]);
  const selected = structuredClone(servingProtocol);
  selected.registration.installedStudy.runtime.postgres.systemIdentifier = "1234567890";
  selected.persistentReceipt.system_identifier = "1234567890";
  const admission = createAdmissionProtocolFixture(t, { configureInput(input, paths) {
    const originalIdentity = input.proposed.registration.cutover;
    const registration = selected.registration, identity = registration.cutover;
    registration.disposable = false;
    registration.root = join(paths.directory, "source"); mkdirSync(registration.root);
    registration.envFile = join(paths.directory, "serving.env"); writeFileSync(registration.envFile, "# protocol fixture only\n", { mode: 0o600 });
    registration.composeFiles = [join(registration.root, "serving.compose.yml")];
    identity.composeFiles = structuredClone(registration.composeFiles);
    identity.phase = "quarantined";
    identity.graphSourceReference = originalIdentity.graphSourceReference;
    identity.graphReceipt = originalIdentity.graphReceipt;
    identity.freshInitReceipt = originalIdentity.freshInitReceipt;
    identity.ingress.hosts = [new URL(registration.webUrl).host];
    registration.installedStudy = { version: 1, originalRegistrationPath: paths.originalRegistrationPath,
      stateDirectory: paths.directory, lockIdentity: targetKey(input.current.registration), receiptPath: paths.receiptPath,
      admissionPath: paths.admissionPath, persistentIntentPath: join(paths.directory, "persistent-intent.json"),
      persistentReceiptPath: join(paths.directory, "persistent-receipt.json"), runtime: registration.installedStudy.runtime };
    selected.configuration.secrets["persistent-postgres-password"].file = join(paths.directory, "postgres-password");
    writeFileSync(selected.configuration.secrets["persistent-postgres-password"].file, "protocol-fixture-not-used", { mode: 0o600 });
    input.transitionId = identity.transitionId;
    input.proposed.registration = registration;
    input.proposed.receipt = { ...input.proposed.receipt, revision: identity.revision, schema: identity.schema,
      images: structuredClone(identity.images), composeFiles: structuredClone(identity.composeFiles), cutover: structuredClone(identity) };
    selected.persistentIntent.source_volume_names = Object.values(input.current.registration.volumes).map(volume => volume.name);
  } });
  const registration = admission.plan.registration, identity = registration.cutover;
  writeFileSync(registration.installedStudy.persistentIntentPath, JSON.stringify(selected.persistentIntent), { mode: 0o600 });
  writeFileSync(registration.installedStudy.persistentReceiptPath, JSON.stringify(selected.persistentReceipt), { mode: 0o600 });
  // Only the canonical producer's semantic fakes execute here. This produces a
  // valid protocol packet and private files, never actual admission or services.
  await executeFreshStartTransition(admission.plan, { state: admission.state, effects: admission.effects() });
  const labels = { "tempo.owner": registration.project, "tempo.target-kind": "persistent", "tempo.disposable": "false" };
  const networkName = selected.persistentIntent.network_name, networkId = "a".repeat(64);
  const ids = { postgres: "7".repeat(64), learning: "8".repeat(64), "study-web": "9".repeat(64) };
  const running = command === "stop";
  const containers = Object.fromEntries(Object.entries(ids).map(([service, Id]) => [service, {
    Id, Image: identity.images[service], Config: { User: service === "learning" ? "10001:10001" : "",
      Labels: { "com.docker.compose.project": registration.project, "com.docker.compose.service": service },
      Env: Object.entries(selected.configuration.services[service].environment ?? {}).map(([name, value]) => `${name}=${value}`) },
    State: { Running: running, Restarting: false },
    Mounts: service === "postgres" ? [{ Type: "volume", Name: selected.persistentIntent.data_volume,
      Destination: registration.installedStudy.runtime.postgres.mountDestination, RW: true }]
      : service === "learning" ? [{ Type: "volume", Name: selected.persistentIntent.runtime_secrets_volume, Destination: "/run/persistent", RW: false }] : [],
    HostConfig: { RestartPolicy: { Name: "no" }, PortBindings: service === "study-web"
      ? { "80/tcp": [{ HostIp: "127.0.0.1", HostPort: "38127" }] } : {} },
    NetworkSettings: { Ports: service === "study-web" ? { "80/tcp": [{ HostIp: "127.0.0.1", HostPort: "38127" }] } : {},
      Networks: { [networkName]: { NetworkID: networkId, IPAddress: service === "postgres" ? "172.22.0.2" : "172.22.0.3" } } },
  }]));
  const calls = [], fetchCalls = [], reply = value => ({ code: 0, stdout: typeof value === "string" ? value : JSON.stringify(value), stderr: "" });
  const transport = { beforeCommand };
  const run = async (binary, argv, settings) => {
    const call = { binary, argv: [...argv], settings,
      lockHeld: existsSync(join(admission.directory, "maintenance.lock")) };
    calls.push(call);
    assert.equal(binary, "docker"); assert.deepEqual(argv.slice(0, 2), ["--context", registration.context]);
    const intercepted = await transport.beforeCommand?.({ ...call, admission, registration, containers, calls, reply });
    if (intercepted !== undefined) return intercepted;
    const args = argv.slice(2);
    if (args[0] === "info") return reply(registration.daemonId);
    if (args[0] === "ps") {
      assert.ok(args.includes(`label=com.docker.compose.project=${admission.plan.original.registration.project}`));
      return reply(""); // The independently inspected original project has no running/restarting writer.
    }
    if (args[0] === "image") return reply([{ Id: args[2], RepoDigests: [] }]);
    if (args[0] === "volume") return reply([{ Name: args[2], CreatedAt: "2026-10-25T00:00:00Z", Labels: labels }]);
    if (args[0] === "network") return reply([{ Name: networkName, Id: networkId, Labels: labels }]);
    if (args[0] === "inspect") {
      const container = Object.values(containers).find(value => value.Id === args[1]);
      assert.ok(container, "Only the selected target containers can be inspected"); return reply([container]);
    }
    if (args[0] === "compose" && args.includes("config")) return reply(selected.configuration);
    if (args[0] === "compose" && args.includes("ps")) return reply(Object.entries(ids).map(([Service, ID]) => ({ Service, ID })));
    if (args[0] === "compose" && args.includes("up")) {
      for (const service of Object.keys(ids).filter(name => args.includes(name))) containers[service].State.Running = true;
      return reply("");
    }
    if (args[0] === "compose" && args.includes("stop")) {
      for (const service of ["learning", "study-web"].filter(name => args.includes(name))) containers[service].State.Running = false;
      return reply("");
    }
    if (args.includes("tempo_learning_database.runtime_readback") && args.includes("--help")) return reply("public read-only verifier help");
    if (args.some(value => String(value).includes("pg_controldata")))
      return reply(`TEMPO_PG_VERSION=18\nDatabase system identifier: ${registration.installedStudy.runtime.postgres.systemIdentifier}\n`);
    if (args.includes("tempo_learning_database.runtime_readback")) return reply({ status: "verified", scope: "observed", evidenceId: "fixture-db-readback", read_only: true,
      schema: identity.schema, workspace_id: identity.workspace, timezone: selected.persistentIntent.timezone, database: identity.database.name,
      roles: { writer: selected.persistentReceipt.roles.writer, reader: selected.persistentReceipt.roles.reader },
      server: { hostaddr: "172.22.0.2", port: 5432, system_identifier: registration.installedStudy.runtime.postgres.systemIdentifier, identity_scope: "caller-bound" } });
    if (args.includes("-c")) return reply({ ready: { status: "ready", workspace_id: identity.workspace },
      bootstrap: { schema_version: 1, workspace_id: identity.workspace, timezone: selected.persistentIntent.timezone },
      study_token_sha256: registration.installedStudy.runtime.credentialDigests.studyTokenSha256,
      runtime_passfile_sha256: registration.installedStudy.runtime.credentialDigests.runtimePassfileSha256 });
    assert.fail(`Unexpected fake command ${JSON.stringify(args)}`);
  };
  const fetcher = async (url, settings) => { fetchCalls.push({ url, settings }); assert.equal(url, registration.webUrl); return { ok: true, status: 200 }; };
  return { admission, registration, configuration: selected.configuration, persistentIntent: selected.persistentIntent,
    persistentReceipt: selected.persistentReceipt, containers, calls, fetchCalls, transport,
    dependencies: { run, fetcher, environment: {} } };
}

