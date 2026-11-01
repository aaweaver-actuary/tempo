"""Both retained publication and repair use the same Repertoire mutation owner."""
import ast
from pathlib import Path


def test_legacy_and_current_edit_callers_share_owner():
    from app import integrity_repair_commands
    from app.repertoire.changes import retained, retained_mutations
    from app.services import repertoire_integrity

    assert retained.replace_repertoire_line is integrity_repair_commands.replace_repertoire_line is retained_mutations.replace_repertoire_line
    assert retained.rewrite_card is integrity_repair_commands.rewrite_card is repertoire_integrity.rewrite_card is retained_mutations.rewrite_card
    for module in (retained, retained_mutations):
        tree = ast.parse(Path(module.__file__).read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert not any('integrity_repair_commands' in ast.unparse(node) for node in imports)
