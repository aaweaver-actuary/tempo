"""Generate the existing Content manifest for a new authored PGN source."""

import hashlib
import io
from typing import Literal

import chess.pgn

from .bridge import ImportManifest, Provenance, RouteAssignment, digest, map_import
from .domain import ContentError
from .models import SourcePolicy, SourceRepertoire


def make_manifest(
    raw: bytes,
    *,
    workspace_id: str,
    source_key: str,
    title: str,
    learner_color: Literal["white", "black"],
) -> ImportManifest:
    """Describe fresh authored routes without inferring identities or history.

    This does not convert historical cards or change an existing source's color.
    The bridge remains authoritative for the resulting manifest/source schema.
    """
    if len(raw) > 16 * 1024 * 1024:
        raise ContentError("source_bound", "Authored PGN exceeds 16 MiB")
    try:
        stream = io.StringIO(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ContentError("invalid_pgn", "Authored PGN must be UTF-8") from error

    repertoire_key = digest(["repertoire", workspace_id, source_key])
    policy_key = digest(["policy", workspace_id, source_key, learner_color])
    assignments: dict[str, RouteAssignment] = {}
    complete_paths = 0
    while (game := chess.pgn.read_game(stream)) is not None:
        if game.errors or game.headers.get("Variant", "Standard").lower() not in (
            "standard", "from position"
        ):
            raise ContentError(
                "invalid_pgn", "Only legal standard/from-position authored PGN is supported"
            )
        board = game.board()
        if not board.is_valid():
            raise ContentError("invalid_fen", "Invalid chess position FEN")
        if not game.variations:
            raise ContentError("invalid_pgn", "Authored PGN must contain moves")
        starting_fen = board.fen(en_passant="fen")
        pending = [(game, [])]
        while pending:
            node, moves = pending.pop()
            if not node.variations:
                complete_paths += 1
                fingerprint = digest([starting_fen, moves])
                assignments[fingerprint] = RouteAssignment(
                    fingerprint=fingerprint,
                    route_key=digest(["route", source_key, fingerprint]),
                    policy_key=policy_key,
                )
            for child in node.variations:
                if len(moves) >= 1000:
                    raise ContentError("route_bound", "Authored route exceeds 1000 plies")
                if not child.move:
                    raise ContentError("invalid_pgn", "Authored routes require legal chess moves")
                pending.append((child, moves + [child.move.uci()]))
                if complete_paths + len(pending) > 10000:
                    raise ContentError(
                        "route_count_bound", "Authored PGN exceeds 10,000 complete paths"
                    )
        if complete_paths > 10000:
            raise ContentError("route_count_bound", "Authored PGN exceeds 10,000 complete paths")

    if not assignments:
        raise ContentError("invalid_pgn", "Authored PGN must contain moves")

    manifest = ImportManifest(
        schema_version=1,
        kind="authored_import",
        workspace_id=workspace_id,
        source_key=source_key,
        title=title,
        complete=True,
        provenance=Provenance(
            kind="authored_export",
            source_sha256=hashlib.sha256(raw).hexdigest(),
            description=title,
        ),
        repertoires=[SourceRepertoire(key=repertoire_key, title=title, learner_color=learner_color)],
        policies=[SourcePolicy(key=policy_key, repertoire_key=repertoire_key)],
        route_assignments=[assignments[key] for key in sorted(assignments)],
    )
    map_import(raw, manifest.model_dump(mode="json"))
    return manifest
