import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

export function buildRestrictedContracts(components, { root, repository, workspace, command, copyInput, artifact, report, fingerprint }) {
  if (!components.length) return [];
  report.sourceInputs.contractRequirements = fingerprint(resolve(root, "contracts/requirements-test.lock"));
  report.sourceInputs.contractCatalog = fingerprint(resolve(root, "contracts/catalog.json"));
  const builds = components.map(component => {
    const directory = join(workspace, component.id);
    mkdirSync(directory);
    for (const input of component.buildInputs) copyInput(component, input, directory);
    writeFileSync(join(directory, "registration.json"), JSON.stringify(component));
    command(component.id + "-boundary", process.env.TEMPO_COMPONENT_PYTHON ?? "python3", [resolve(repository, "tools/component-build/check-contract.py"), directory, JSON.stringify(component), resolve(root, component.catalog)], directory);
    return { component, directory };
  });
  for (const version of ["3.12", "3.14"]) {
    const results = join(workspace, "contract-results-" + version);
    mkdirSync(results);
    const environment = join(workspace, "contract-python-" + version);
    command("contract-" + version + "-environment", "uv", ["venv", "--python", version, environment], workspace);
    const python = join(environment, "bin/python");
    command("contract-" + version + "-runtime", python, ["--version"], workspace);
    command("contract-" + version + "-dependencies", "uv", ["pip", "install", "--python", python, "--require-hashes", "-r", resolve(root, "contracts/requirements-test.lock")], workspace);
    for (const { component, directory } of builds) {
      if (version === "3.12") {
        command(component.id + "-build", python, ["-m", "build", "--no-isolation"], directory);
        for (const name of readdirSync(join(directory, "dist"))) artifact(join(directory, "dist", name));
        rmSync(join(directory, "src"), { recursive: true });
      }
      for (const [format, suffix] of [["wheel", ".whl"], ["sdist", ".tar.gz"]]) {
        const distribution = join(directory, "dist", readdirSync(join(directory, "dist")).find(name => name.endsWith(suffix)));
        command(component.id + "-" + version + "-" + format + "-install", "uv", ["pip", "install", "--python", python, "--no-deps", "--no-build-isolation", "--reinstall", distribution], workspace);
        command(component.id + "-" + version + "-" + format + "-exports", python, ["-I", resolve(repository, "tools/component-build/verify-contract-artifact.py"), join(directory, "registration.json"), resolve(root, component.catalog), directory], workspace);
        const junit = join(results, component.id + "-" + version + "-" + format + ".xml");
        command(component.id + "-" + version + "-" + format + "-tests", python, ["-I", "-m", "pytest", join(directory, "tests"), "--import-mode=importlib", "--rootdir=" + workspace, "-q", "--junitxml=" + junit], workspace);
      }
    }
    command("contract-" + version + "-executed-identities", python, [resolve(repository, "tools/component-build/case-results.py"), results], workspace);
    command("contract-" + version + "-installed-dependencies", "uv", ["pip", "freeze", "--python", python], workspace);
  }
  return builds.map(({ directory }) => join(directory, "dist"));
}
