"""Bundle this owner's centralized database files into standalone artifacts."""
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        local = Path(self.root) / "database/tempo_remediation_database"
        repository = Path(self.root).parents[2] / "database/tempo_remediation_database"
        source = local if local.is_dir() else repository
        if not source.is_dir():
            raise FileNotFoundError("Missing tempo_remediation_database build input")
        destination = "tempo_remediation_database" if self.target_name == "wheel" else "database/tempo_remediation_database"
        build_data["force_include"][str(source)] = destination
