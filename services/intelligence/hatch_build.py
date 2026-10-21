"""Bundle this owner's centralized database files into standalone artifacts."""
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        local = Path(self.root) / "database/tempo_intelligence_database"
        repository = Path(self.root).parents[1] / "database/tempo_intelligence_database"
        source = local if local.is_dir() else repository
        if not source.is_dir():
            raise FileNotFoundError("Missing tempo_intelligence_database build input")
        if self.target_name == "wheel":
            for path in source.iterdir():
                if path.name not in {"tests", "tooling", "__pycache__"}:
                    build_data["force_include"][str(path)] = "tempo_intelligence_database/" + path.name
        else:
            build_data["force_include"][str(source)] = "database/tempo_intelligence_database"
