"""Keep legacy-adapter native tooling in source distributions only."""
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "sdist":
            return
        local = Path(self.root) / "database/tempo_legacy_database"
        repository = Path(self.root).parents[2] / "database/tempo_legacy_database"
        owner = local if local.is_dir() else repository
        for category in ("tooling/learning_review", "tests/learning_review"):
            source = owner / category
            if not source.is_dir():
                raise FileNotFoundError("Missing Learning review database build input: " + category)
            build_data["force_include"][str(source)] = "database/tempo_legacy_database/" + category
