"""A restore drill must reject missing schema and changed cutover-only rows."""

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import verify_postgres_backup as backup_verification


def test_postgres_backup_comparison_rejects_missing_table_and_changed_rows(monkeypatch):
    source = object()
    restored = object()
    table = {"operation_receipts": (("operation_id",), (("operation_id", "text", True),))}
    monkeypatch.setattr(
        backup_verification, "table_layout",
        lambda database: table if database is source else {},
    )
    with pytest.raises(RuntimeError, match="missing=.*operation_receipts"):
        backup_verification.compare_backup(source, restored)

    monkeypatch.setattr(backup_verification, "table_layout", lambda _database: table)
    monkeypatch.setattr(
        backup_verification, "destination_fingerprint",
        lambda database, *_arguments: (1, "source") if database is source else (1, "changed"),
    )
    with pytest.raises(RuntimeError, match="Restore row mismatch in operation_receipts"):
        backup_verification.compare_backup(source, restored)


def test_postgres_backup_comparison_projects_postgres_only_columns(monkeypatch):
    source = object()
    restored = object()
    columns = (("operation_id", "text", True), ("response_json", "text", False))
    monkeypatch.setattr(
        backup_verification, "table_layout",
        lambda _database: {"operation_receipts": (("operation_id",), columns)},
    )
    compared = []
    monkeypatch.setattr(
        backup_verification, "destination_fingerprint",
        lambda database, table, names, primary_key, text_primary_key:
        compared.append((database, table, names, primary_key, text_primary_key)) or (1, "same"),
    )

    backup_verification.compare_backup(source, restored)

    assert compared == [
        (source, "operation_receipts", ["operation_id", "response_json"], ["operation_id"], set()),
        (restored, "operation_receipts", ["operation_id", "response_json"], ["operation_id"], set()),
    ]
