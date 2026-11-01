"""Bind the existing application authorities to legacy database access."""


def bind_database_infrastructure() -> None:
    # Imports are delayed until composition so importing the storage owner is inert.
    from app.services.activity_gate import activity_gate
    from app.services.background_runtime import database_stage
    from tempo_legacy_database.infrastructure import bind_database_infrastructure as bind

    bind(activity_gate, database_stage)
