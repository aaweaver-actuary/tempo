import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export function readManifest(root) {
  return JSON.parse(readFileSync(resolve(root, "components.json"), "utf8"));
}
export function parseSelection(argumentsList) {
  const options = { positional: [] };
  for (let index = 0; index < argumentsList.length; index++) {
    const argument = argumentsList[index];
    if (["--component", "--suite"].includes(argument)) {
      const value = argumentsList[++index];
      if (!value || value.startsWith("--")) throw new Error("Empty " + argument + " selection");
      const key = argument === "--component" ? "componentIds" : "suiteIds";
      (options[key] ??= []).push(value);
    } else if (argument === "--list") options.list = true;
    else if (argument === "--consumers") options.consumers = true;
    else if (argument.startsWith("--")) throw new Error("Unknown option: " + argument);
    else options.positional.push(argument);
  }
  return options;
}
export function selectComponents(manifest, componentIds, defaults) {
  const requested = componentIds ?? defaults ?? manifest.components.filter(component => component.status === "implemented").map(component => component.id);
  if (!requested.length) throw new Error("Empty component selection");
  const selected = new Map();
  function include(id) {
    const component = manifest.components.find(candidate => candidate.id === id);
    if (!component) throw new Error("Unknown component: " + id);
    if (component.status !== "implemented") throw new Error("Component is planned, not implemented: " + id);
    if (selected.has(id)) return;
    selected.set(id, component);
    for (const dependency of component.dependencies) {
      const owner = manifest.components.find(candidate => candidate.id === dependency || candidate.artifact?.name === dependency);
      if (owner) include(owner.id);
    }
  }
  for (const id of requested) include(id);
  return [...selected.values()];
}
export function selectSuites(manifest, suiteIds) {
  if (!suiteIds?.length) throw new Error("Empty suite selection");
  return [...new Set(suiteIds)].map(id => {
    const suite = manifest.suites?.find(candidate => candidate.id === id);
    if (!suite) throw new Error("Unknown suite: " + id);
    return suite;
  });
}
