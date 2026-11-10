import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { test } from "node:test";

const root = new URL("../../", import.meta.url);
const read = path => readFileSync(new URL(path, root), "utf8");
const rules = read("Dockerfile.web.dockerignore").split(/\r?\n/).filter(line => line && !line.startsWith("#"));
const dockerfile = read("Dockerfile.web");

test("web build context retains each workspace and source input", () => {
  const packageJson = JSON.parse(read("package.json"));
  for (const workspace of packageJson.workspaces) {
    assert(rules.includes(`!${workspace}/package.json`), `${workspace} manifest must enter context`);
    assert(rules.includes(`!${workspace}/src/**`), `${workspace} source must enter context`);
    assert(dockerfile.includes(`COPY ${workspace}/package.json ./${workspace}/package.json`), `${workspace} manifest must precede npm ci`);
    assert(existsSync(new URL(`${workspace}/tsconfig.json`, root)));
  }
  for (const input of ["Cargo.toml", "Cargo.lock", "tempo-core/**", "tests/fixtures/**", "tsconfig.json", "module-resolution.ts", "vite.static.config.ts", "nginx.conf", "static/**", "app/**", "database/browser/**", "packages/board-ui/src/**", "packages/board-ui/styles/**", "packages/board-ui/assets/**", "scripts/study-composition.ts", "scripts/offline-shell-assets.ts", "public/**"]) {
    assert(rules.includes(`!${input}`), `missing build input ${input}`);
  }
  assert.match(dockerfile, /COPY --from=core \/app\/public\/tempo-core/);
  assert.match(dockerfile, /RUN npm run build:local/);
});

test("web build context ends with exclusions for secrets, generated files and documentation", () => {
  assert.equal(rules[0], "**", "Unlisted roots must stay out of the context");
  const lastInclude = rules.findLastIndex(rule => rule.startsWith("!"));
  for (const excluded of ["**/.env*", "**/.git", "**/.git/**", "**/node_modules", "**/node_modules/**", "**/.venv", "**/.venv/**", "**/.tempo-pg-test-secrets-*", "**/.tempo-pg-test-secrets-*/**", "**/dist", "**/dist/**", "**/target", "**/target/**", "public/tempo-core", "public/tempo-core/**", "**/*.md", "**/AGENTS.md"]) {
    assert(rules.indexOf(excluded) > lastInclude, `${excluded} must override all inclusions`);
  }
});
