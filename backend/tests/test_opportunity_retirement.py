"""Ordinary Study and saved graph facts work without the opportunity producer."""

import importlib

import pytest

from app import main


def test_retired_opportunity_modules_are_physically_absent_after_application_composition():
    for name in (
        "app.services.repertoire_opportunities",
        "app.opportunity_commands",
        "tempo_legacy_database.adapters.authored_repertoires.opportunities.storage",
        "tempo_legacy_database.adapters.authored_repertoires.discovery_commands",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
    assert main.app is not None
