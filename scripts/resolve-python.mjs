import { existsSync } from "node:fs";

export function resolvePython() {
  return process.env.TEMPO_PYTHON || (existsSync(".venv/bin/python") ? ".venv/bin/python"
    : existsSync("backend/.venv/bin/python") ? "backend/.venv/bin/python" : "python3");
}
