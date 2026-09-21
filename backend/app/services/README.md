# Backend services

This directory contains Python services behind the FastAPI routes. It is
currently a flat compatibility namespace; the following clusters describe the
intended ownership while a future refactor can introduce subpackages.

| Cluster | Files to look at first |
| --- | --- |
| Game ingestion | `game_sync.py`, `game_sync_coordinator.py`, `game_record.py`, `game_normalizer.py`, provider clients |
| Game insights | `game_analysis.py`, `game_findings.py`, `gameplay_events.py`, `guided_review.py`, `statistics.py` |
| Repertoire | `repertoire_comparison.py`, `repertoire_conflicts.py`, `repertoire_coverage.py`, `repertoire_integrity.py`, `introduction_priorities.py` |
| Learning | `cards.py`, `review_service.py`, `scheduler.py`, `prefix_split.py`, `pgn.py` |
| Tactics | `puzzles.py`, `tactical_catalog.py`, `motif_detectors.py`, `tactical_opportunities.py` |
| Platform | `activity_gate.py`, `analysis.py`, `endgames.py` |

Several clusters share position replay, canonical FEN, provider error, and
background-job concerns. Consolidate those contracts only after preserving
their named tests. See [the organization audit](../../docs/CODE-ORGANIZATION-AUDIT.md).
