"""Production Study + installed Learning + owned PostgreSQL acceptance."""
from pathlib import Path
import hashlib, json, os, subprocess, sys, tempfile, shutil
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "database"))
from tempo_learning_database.tooling.run_postgres import ROOT, main


def study_test_paths(profile: str) -> list[Path]:
    study_tests = ROOT / "services/learning/runtime/integration/study_tests"
    if profile == "full":
        return [study_tests]
    if profile == "essential":
        test_file = study_tests / "test_study_ui.py"
        return [
            Path(f"{test_file}::test_production_study_learning_native[{scenario}]")
            for scenario in (
                "normal",
                "teaching",
                "lost-response",
                "before-commit",
                "next-load",
                "two-tabs",
                "offline-shell",
                "wrong-credential",
                "origin-transition",
                "quota",
            )
        ]
    raise ValueError(f"Unknown study profile: {profile!r}")


def run(profile: str = "full"):
    test_paths = study_test_paths(profile)
    if len(sys.argv) > 2:
        raise SystemExit("Pass at most one absolute task-owned evidence directory")
    evidence = Path(sys.argv[1]) if len(sys.argv) == 2 else ROOT / "test-results/learning-full-study"
    if not evidence.is_absolute():
        raise SystemExit("Evidence directory must be absolute")
    evidence.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix="tempo-f2-study-build-"))
    try:
        allowed = {"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "CI", "PLAYWRIGHT_BROWSERS_PATH", "NODE_EXTRA_CA_CERTS"}
        environment = {key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")}
        environment.update(TEMPO_BROWSER_BUILD_DIR=str(build), TZ="America/New_York")
        installed = subprocess.run(["uv", "sync", "--project", str(ROOT / "services/learning/runtime"),
                                   "--locked", "--no-editable", "--reinstall-package", "tempo-learning-runtime"],
                                  env=environment, capture_output=True, text=True)
        (evidence / "study-learning-install.log").write_text(installed.stdout + installed.stderr)
        if installed.returncode:
            raise RuntimeError("Current installed Learning unavailable; see study-learning-install.log")
        result = subprocess.run(["npm", "run", "build:study"], cwd=ROOT,
                                env=environment, capture_output=True, text=True)
        (evidence / "study-build.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("Production Study build failed; see study-build.log")
        (evidence / "study-assets.json").write_text(json.dumps({str(path.relative_to(build)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(build.rglob("*")) if path.is_file()}, indent=2))
        runner_options = {"evidence_directory": evidence}
        if profile == "essential":
            runner_options["fixture_profile"] = "essential"
        main(test_paths,
             {"TEMPO_LEARNING_STUDY_ASSETS": str(build)},
             [ROOT / "app", ROOT / "static", ROOT / "vite.static.config.ts",
              ROOT / "package.json", ROOT / "package-lock.json", ROOT / "module-resolution.ts",
              ROOT / "scripts/prove-study-learning.mjs", ROOT / "scripts/offline-shell-assets.ts",
              ROOT / "scripts/maia-cache-asset.ts", ROOT / "database/browser",
              ROOT / "database/browser-intents", ROOT / "database/tests/browser", ROOT / "public"],
             **runner_options)
    finally:
        shutil.rmtree(build)
        (evidence / "study-build-cleanup.json").write_text(json.dumps({"removed": not build.exists()}))


if __name__ == "__main__":
    run()
