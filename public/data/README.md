# Packaged data

This directory contains validated tactic catalog metadata and packaged puzzle
decks used by the static build and local preload paths. The backend validates
records through `backend/app/services/puzzles.py`; generated/large collections
should not be hand-edited without rerunning validation.
