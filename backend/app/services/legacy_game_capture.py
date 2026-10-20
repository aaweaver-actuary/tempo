"""Finite complete source traversal with bounded retained decision evidence.

No connection survives a reader call. This is an ephemeral capture, not an index
or second work frontier. A generation change, missing EOF or budget exhaustion
invalidates the whole observation; no partial proof can authorize admission.
"""
from dataclasses import dataclass
import json
import time

import chess
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import (authority_source_projection,
    authority_graph_digest, canonical)
from tempo_game_evidence_contracts.legacy_scope import (LegacyDecisionScope,
    LegacyRowDigest, LegacyScopedCatalogueProof, LegacyStreamManifest)
from tempo_learning_remediation import RemediationError

PAGE_SIZE = 64


@dataclass(frozen=True)
class CaptureBudget:
    max_rows: int
    max_pages: int
    max_seconds: float
    max_bytes: int
    max_candidates: int = 64
    max_graphs: int = 256
    max_cards: int = 128
    max_row_bytes: int = 65536


# Trusted owner budget, never accepted from an HTTP envelope. Preflight must
# fit this total across ALL five streams before a traversal starts. This is a
# finite source-only checkpoint budget; actual qualification remains separate.
DEFAULT_BUDGET = CaptureBudget(32768, 520, 20.0, 32 * 1024 * 1024)


@dataclass(frozen=True)
class CaptureRequest:
    repertoire_id: str
    game_id: str
    decision_plies: tuple[int, ...]


@dataclass(frozen=True)
class Stream:
    name: str
    columns: str
    source: str
    keys: tuple[str, ...]
    parameters: tuple = ()

    def key(self, row):
        return tuple(row[key.rsplit(".", 1)[-1]] for key in self.keys)


class Traversal:
    def __init__(self, reader, streams, budget, clock=time.monotonic):
        self.reader, self.budget, self.clock = reader, budget, clock
        self.deadline = clock() + budget.max_seconds
        self.pages = self.rows = self.bytes = 0
        self.preflight = {}
        if (min(budget.max_rows, budget.max_pages, budget.max_bytes) < 1
                or budget.max_seconds <= 0 or not 1 <= budget.max_candidates <= 64):
            raise ValueError("finite positive capture budget required")
        for stream in streams:
            self.check_time()
            count = reader(f"/*legacy-stream:{stream.name}:count*/ SELECT COUNT(*) row_count FROM " +
                stream.source, stream.parameters)[0]["row_count"]
            last = reader(f"/*legacy-stream:{stream.name}:highwater*/ SELECT " + ",".join(stream.keys) +
                " FROM " + stream.source + " ORDER BY " + ",".join(key + " DESC" for key in stream.keys) +
                " LIMIT 1", stream.parameters)
            if type(count) is not int or count < 0 or bool(count) != bool(last):
                self.fail("legacy_capture_preflight_changed")
            self.preflight[stream.name] = (count, stream.key(last[0]) if last else ())
        if (sum(count for count, _ in self.preflight.values()) > budget.max_rows
                or sum(count // PAGE_SIZE + 1 for count, _ in self.preflight.values()) > budget.max_pages):
            self.fail("legacy_capture_preflight_exceeds_budget")

    @staticmethod
    def fail(reason):
        raise RemediationError("stale_revision", reason)

    def check_time(self):
        if self.clock() >= self.deadline:
            self.fail("legacy_capture_deadline")

    def walk(self, stream, project):
        expected, highwater = self.preflight[stream.name]
        cursor = tuple(-1 if key.endswith("decision_index") else "" for key in stream.keys)
        digest, visited = LegacyRowDigest(), 0
        last_key = ()
        while expected:
            self.check_time()
            if self.pages >= self.budget.max_pages:
                self.fail("legacy_capture_page_budget")
            keys = "ROW(" + ",".join(stream.keys) + ")"
            marks = "ROW(" + ",".join("%s" for _ in stream.keys) + ")"
            # PostgreSQL compares seek/highwater keys in its own collation.
            statement = (f"/*legacy-stream:{stream.name}:page*/ SELECT " + stream.columns +
                f",{keys}>{marks} AS _seek_advanced FROM " + stream.source +
                f" AND {keys}>{marks} AND {keys}<={marks} ORDER BY " + ",".join(stream.keys) + " LIMIT 64")
            page = self.reader(statement, (*cursor, *stream.parameters, *cursor, *highwater))
            self.pages += 1
            if len(page) > PAGE_SIZE:
                self.fail("legacy_capture_page_invalid")
            seen = set()
            for original in page:
                self.check_time()
                row = dict(original)
                if row.pop("_seek_advanced", None) is not True:
                    self.fail("legacy_capture_seek_not_advanced")
                key = stream.key(row)
                if key in seen or key == cursor:
                    self.fail("legacy_capture_seek_not_advanced")
                seen.add(key)
                visited += 1
                self.rows += 1
                size = len(canonical(row).encode())
                if size > self.budget.max_row_bytes:
                    self.fail("legacy_capture_row_byte_budget")
                self.bytes += size
                try:
                    digest.add(project(row))
                except (ValueError, TypeError, KeyError) as error:
                    raise RemediationError("stale_revision", "legacy_capture_uncertain_source") from error
                if (visited > expected or self.rows > self.budget.max_rows
                        or self.bytes > self.budget.max_bytes):
                    self.fail("legacy_capture_row_or_byte_budget")
                last_key = key
                yield row
                self.check_time()
            if len(page) < PAGE_SIZE:
                break
            cursor = last_key
        self.check_time()
        if visited != expected or last_key != highwater:
            self.fail("legacy_capture_incomplete_exhaustion")
        self.manifest = LegacyStreamManifest(name=stream.name, expected_rows=expected,
            visited_rows=visited, highwater=list(highwater), last_key=list(last_key),
            digest=digest.hexdigest(), exhausted=True)


def source_row(name, row):
    if name == "graphs":
        return {"source_row_digest": authority_graph_digest([row])}
    tables = {key: [] for key in ("repertoires", "repertoire_lines", "cards", "repertoire_cards")}
    tables[name] = [row]
    return authority_source_projection(tables)[name][0]


def capture_scope(delivery, reader, metadata, *, budget=DEFAULT_BUDGET, clock=time.monotonic):
    request = CaptureRequest(delivery.authority.repertoire_id, delivery.game_id,
        tuple(sorted({item.ply for item in delivery.encounters})))
    return capture_source_scope(request, reader, metadata, budget=budget, clock=clock)


def capture_source_scope(request: CaptureRequest, reader, metadata, *, budget=DEFAULT_BUDGET, clock=time.monotonic):
    """Read-only inspection port; no obligation/delivery/attempt is constructed."""
    from .legacy_game_authority import (Capture, current_token, completed_game_time, introduced_before_game, comparison_key,
                                       _CARD_COLUMNS, _GAME_COLUMNS, _REPERTOIRE_COLUMNS)
    from .legacy_game_evidence_scope import LEGACY_WORKSPACE_ID
    repertoire_id = request.repertoire_id
    before = metadata(repertoire_id)
    if before["graph_state"] != "ready" or not before.get("integrity_current"):
        Traversal.fail("legacy_completed_current_integrity_required")
    identities = reader("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1")
    games = reader("SELECT " + _GAME_COLUMNS + " FROM imported_games WHERE id=%s", (request.game_id,))
    if len(identities) != 1 or identities[0]["workspace_id"] != LEGACY_WORKSPACE_ID or len(games) != 1:
        Traversal.fail("legacy_capture_header_missing")
    game = games[0]
    occurred_at = completed_game_time(game)
    board = chess.Board(game["start_fen"])
    if not board.is_valid():
        Traversal.fail("legacy_invalid_source_position")
    root = canonical_fen_key(board.fen(en_passant="legal"))
    moves = json.loads(game["moves_json"])
    scope = LegacyDecisionScope(game_id=request.game_id, content_hash=game["content_hash"], occurred_at=occurred_at.isoformat(),
        learner_color=game["color"], starting_position_key=root, moves=moves,
        decision_plies=list(request.decision_plies))
    streams = [
        Stream("repertoires", _REPERTOIRE_COLUMNS, "repertoires WHERE left(id,2)<>'__'", ("id",)),
        Stream("repertoire_lines", "id,repertoire_id,trained_color,start_fen,moves_json",
            "repertoire_lines WHERE left(repertoire_id,2)<>'__'", ("id",)),
        Stream("graphs", "*", "opening_graph_steps WHERE repertoire_id=%s AND generation=%s",
            ("line_id", "decision_index"), (repertoire_id, before["graph_generation"])),
        Stream("cards", ",".join("card." + key for key in _CARD_COLUMNS.split(",")) +
            ",EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id) integrity_blocked,"
            "EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id) deleted",
            "cards card WHERE (EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id=%s) "
            "OR EXISTS(SELECT 1 FROM opening_graph_steps graph WHERE graph.card_id=card.id AND graph.repertoire_id=%s "
            "AND graph.generation=%s)) AND card.content_type='opening'",
            ("card.id",), (repertoire_id, repertoire_id, before["graph_generation"])),
        Stream("repertoire_cards", "link.repertoire_id,link.card_id,link.canonical_route_source",
            "repertoire_cards link WHERE (EXISTS(SELECT 1 FROM repertoire_cards selected "
            "WHERE selected.card_id=link.card_id AND selected.repertoire_id=%s) OR EXISTS(SELECT 1 FROM opening_graph_steps graph "
            "WHERE graph.card_id=link.card_id AND graph.repertoire_id=%s AND graph.generation=%s))",
            ("link.repertoire_id", "link.card_id"), (repertoire_id, repertoire_id, before["graph_generation"])),
    ]
    traversal = Traversal(reader, streams, budget, clock)
    tables = {name: [] for name in ("repertoires", "repertoire_lines", "cards", "repertoire_cards")}
    graphs, manifests = [], []
    prefixes = [set() for _ in range(max(scope.decision_plies) + 1)]
    candidate_ids, card_ids = set(), set()
    repertoire_ids, policy_entries = set(), 0
    route_frames = {}
    membership_counts = {}
    for stream in streams:
        for row in traversal.walk(stream, lambda row, name=stream.name: source_row(name, row)):
            if stream.name == "repertoires":
                tables[stream.name].append(row)
                repertoire_ids.add(row["id"])
            elif stream.name == "repertoire_lines":
                try:
                    route_moves = json.loads(row["moves_json"])
                    route_board = chess.Board(row["start_fen"])
                    if (not route_board.is_valid() or row["trained_color"] not in {"white", "black"}
                            or not isinstance(route_moves, list) or len(route_moves) > 1000
                            or any(not isinstance(move, str) for move in route_moves)
                            or row["repertoire_id"] not in repertoire_ids):
                        raise ValueError("unknown route")
                    compatible = (row["trained_color"] == scope.learner_color and
                        canonical_fen_key(route_board.fen(en_passant="legal")) == root)
                    common = 0
                    while compatible and common < min(len(moves), len(route_moves)) and moves[common] == route_moves[common]:
                        common += 1
                    retain = compatible and any(ply <= common and ply < len(route_moves) for ply in scope.decision_plies)
                    frames = [comparison_key(route_board)] if retain else None
                    for move in route_moves:
                        route_board.push_uci(move)
                        if frames is not None: frames.append(comparison_key(route_board))
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    raise RemediationError("stale_revision", "legacy_catalogue_uncertain_source") from error
                if not compatible:
                    continue
                for ply in range(min(common, len(prefixes) - 1) + 1):
                    if row["repertoire_id"] not in prefixes[ply]:
                        policy_entries += 1
                        if policy_entries > 10000:
                            traversal.fail("legacy_capture_policy_context_budget")
                        prefixes[ply].add(row["repertoire_id"])
                if retain:
                    candidate_ids.add(row["id"])
                    if len(candidate_ids) > budget.max_candidates:
                        traversal.fail("legacy_capture_candidate_budget")
                    tables[stream.name].append(row)  # Includes foreign/unbound/unsupported candidates.
                    route_frames[row["id"]] = (route_moves, frames)
            elif stream.name == "graphs":
                if row["line_id"] in candidate_ids:
                    route_moves, frames = route_frames[row["line_id"]]
                    try:
                        executable = json.loads(row["moves_json"])
                        if not isinstance(executable, list) or not 1 <= len(executable) <= 1000 or any(not isinstance(move, str) for move in executable):
                            raise ValueError("uncertain graph executable")
                        graph_board = chess.Board(row["starting_fen"])
                        if not graph_board.is_valid():
                            raise ValueError("invalid graph position")
                        graph_position = comparison_key(graph_board)
                        # Execution equivalence is separate from the raw source
                        # bytes retained and hashed by the traversal owner.
                        roots = [index for index, position in enumerate(frames)
                            if position == graph_position and route_moves[index:index + len(executable)] == executable]
                        relevant = any(ply <= len(route_moves) and route_moves[:ply] == moves[:ply]
                            and root <= ply < root + len(executable) for root in roots for ply in scope.decision_plies)
                    except (ValueError, TypeError, KeyError):
                        # A malformed potentially relevant binding cannot prove
                        # non-overlap. Fail closed rather than discard it.
                        traversal.fail("legacy_capture_uncertain_physical_binding")
                    if not relevant:
                        continue
                    graphs.append(row)
                    card_ids.add(row["card_id"])
                    if len(graphs) > budget.max_graphs or len(card_ids) > budget.max_cards:
                        traversal.fail("legacy_capture_physical_candidate_budget")
            elif stream.name == "cards":
                if row["id"] in card_ids:
                    row["introduced_before_game"] = introduced_before_game(row.get("introduced_at"), occurred_at)
                    tables[stream.name].append(row)
            elif row["card_id"] in card_ids:
                membership_counts[row["card_id"]] = membership_counts.get(row["card_id"], 0) + 1
                if membership_counts[row["card_id"]] > 100:
                    traversal.fail("legacy_capture_membership_budget")
                tables[stream.name].append(row)
        manifests.append(traversal.manifest)
        if metadata(repertoire_id) != before:
            traversal.fail("legacy_source_changed_during_capture")
    if sum(map(len, prefixes)) > 10000:
        traversal.fail("legacy_capture_policy_context_budget")
    provisional = LegacyScopedCatalogueProof(schema_version=1, digest_algorithm="legacy-row-stream-v2",
        authority_id="legacy-graph:" + "0" * 64, catalogue_generation=before["catalogue_generation"],
        integrity_certificate=before["integrity_certificate"],
        scope=scope, streams=manifests, candidate_route_ids=[row["id"] for row in tables["repertoire_lines"]],
        prefix_policies=[sorted(items) for items in prefixes])
    captured = Capture(identities[0]["target_id"], before, tables, graphs, game,
        provisional, provisional.source_identity_digests())
    token = current_token(captured, repertoire_id)
    proof = provisional.model_copy(update={"authority_id": token.storage_identity()[0]})
    return Capture(captured.target_id, before, tables, graphs, game, proof, captured.digests)
