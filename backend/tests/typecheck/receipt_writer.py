"""Run mypy with --warn-unused-ignores to check opaque receipt authority."""

from tempo_storage.commands.receipts.storage import ReceiptWriter


def no_driver_authority(writer: ReceiptWriter) -> None:
    writer.raw  # type: ignore[attr-defined]
    writer.execute("SELECT 1")  # type: ignore[attr-defined]
    writer.commit()  # type: ignore[attr-defined]
    writer.close()  # type: ignore[attr-defined]
