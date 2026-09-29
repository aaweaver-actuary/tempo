"""Exercise every declared GET route through the PostgreSQL reader role."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode

from fastapi.testclient import TestClient
import psycopg

from app.main import app


ROUTES = Path(__file__).resolve().parents[1] / "docs" / "postgres-route-contract.json"


def representative_values(database: psycopg.Connection) -> dict[str, str]:
    def first(statement: str, fallback: str = "audit-missing") -> str:
        row = database.execute(statement).fetchone()
        return str(row[0]) if row and row[0] is not None else fallback

    repertoire_id = first(
        "SELECT id FROM repertoires WHERE id NOT IN "
        "('__tactics__','__endgames__','__game_mistakes__','__defense__') "
        "ORDER BY is_main DESC,id LIMIT 1"
    )
    card_id = first("SELECT id FROM cards WHERE archived=0 ORDER BY id LIMIT 1")
    game_id = first(
        "SELECT id FROM imported_games WHERE analysis_state='ready' ORDER BY id LIMIT 1"
    )
    return {
        "study_id": first("SELECT id FROM studies ORDER BY id LIMIT 1"),
        "chapter_id": first("SELECT id FROM study_chapters ORDER BY id LIMIT 1"),
        "exercise_id": first("SELECT id FROM study_exercises ORDER BY id LIMIT 1"),
        "attempt_id": first("SELECT id FROM study_attempts ORDER BY id LIMIT 1"),
        "operation_id": first("SELECT operation_id FROM operation_receipts ORDER BY operation_id LIMIT 1"),
        "identifier": repertoire_id,
        "card_id": card_id,
        "repertoire_id": repertoire_id,
        "opportunity_id": first(
            "SELECT id FROM repertoire_opportunities WHERE status='active' AND card_id IS NULL "
            "ORDER BY id LIMIT 1"
        ),
        "intent_id": first("SELECT id FROM discovery_admission_intents ORDER BY id LIMIT 1"),
        "candidate_id": first(
            "SELECT id FROM threat_training_candidates WHERE card_id IS NOT NULL ORDER BY id LIMIT 1"
        ),
        "game_id": game_id,
        "session_id": first("SELECT id FROM guided_review_sessions ORDER BY id LIMIT 1"),
        "database_name": "invalid",  # Rejected before any external Explorer request.
        "fen": first("SELECT start_fen FROM cards ORDER BY id LIMIT 1"),
    }


def route_url(path: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        parameter = match.group(1).split(":", 1)[0]
        if parameter == "identifier" and path.startswith("/api/cards/"):
            parameter = "card_id"
        return quote(values[parameter], safe="")

    url = re.sub(r"\{([^{}]+)\}", replace, path)
    if path == "/api/explorer/{database_name}":
        # This is a fixed public chess starting position, never restored data.
        # The invalid database name is rejected before httpx is constructed.
        return url + "?" + urlencode({
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        })
    if path in {"/api/repertoires/{identifier}/annotations",
                "/api/games/position-summary"}:
        url += "?" + urlencode({"fen": values["fen"]})
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_DATABASE_READ_URL"))
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    if not options.dsn:
        parser.error("Provide --dsn or TEMPO_DATABASE_READ_URL")
    if os.getenv("TEMPO_DATABASE_WRITE_URL"):
        parser.error("A read-route audit must not have a write credential")
    with psycopg.connect(options.dsn) as database:
        if database.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("GET audit requires a read-only PostgreSQL role")
        values = representative_values(database)
    rows = [route for route in json.loads(ROUTES.read_text()) if route["method"] == "GET"]
    client = TestClient(app, raise_server_exceptions=False)
    results = []
    for route in rows:
        url = route_url(route["path"], values)
        started = time.perf_counter()
        response = client.get(url)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        detail = None
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail", ""))[:250]
            except Exception:
                detail = response.text[:250]
        results.append({"path": route["path"], "status": response.status_code,
                        "elapsed_ms": elapsed_ms, "detail": detail})
        print(f"{response.status_code:3d} {elapsed_ms:8.1f}ms {route['path']}"
              + (f" — {detail}" if detail else ""), flush=True)
    client.close()
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps({"routes": results}, indent=2) + "\n")
    print(f"Audited {len(results)} GET routes; "
          f"{sum(row['status'] >= 500 for row in results)} returned 5xx", flush=True)
    if len(results) != 59 or any(row["status"] >= 500 and row["path"] != "/api/health"
                                 for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
