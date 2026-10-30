#!/usr/bin/env python3
"""Collect a frozen three-month history using Tempo's existing provider adapters."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "database")]

from app.product_composition.game_history_scripts import entrypoint

if __name__ == "__main__":
    raise SystemExit(entrypoint())
