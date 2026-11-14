"""Pure repertoire projections over supplied bounded owner facts.

Game capture and repertoire-only reads retain separate scopes. SQL, connection,
transaction, graph migration and provider lifecycle authority stay in adapters.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from types import MappingProxyType

import chess
from tempo_chess_core import canonical_fen_key
from tempo_evidence_math import opening_position_key
from tempo_game_evidence_contracts.legacy import (
    decision_identity,
    fingerprint,
)
from tempo_game_review_contracts import (
    AuthoredRoute,
    CanonicalGame,
    RepertoireSnapshotBoundExceeded,
)
from tempo_game_review_contracts import (
    RepertoireSnapshot as MembershipSnapshot,
)

from app.contracts.product_v02.repertoire import (
    DecisionPresentation,
    IntroductionGameScope,
    RepertoireSnapshot,
    RouteBoundary,
)

MAX_CAPTURE_BYTES = 32 * 1024 * 1024


def _introduction(card):
    if "introduced_at" not in card:
        return "unknown", None
    value = card["introduced_at"]
    if value is None:
        return "unintroduced", None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if (
            "T" in value
            and parsed.tzinfo is not None
            and parsed.utcoffset() == UTC.utcoffset(parsed)
        ):
            return "introduced", value
    except (ValueError, TypeError, AttributeError):
        pass
    # A day-only or malformed timestamp cannot establish precise chronology.
    return "unknown", None


def _chronology_provider(value):
    # The retained importer uses "chess.com"; product contracts use "chesscom".
    # Normalize only those explicit aliases, never the source value or digest.
    if value in {"chess.com", "chesscom"}:
        return "chesscom"
    if value == "lichess":
        return "lichess"
    raise ValueError("unsupported_chronology_provider")


def _comparison_scope(capture, comparison_game):
    if comparison_game is None:
        return None
    game = CanonicalGame.model_validate(comparison_game.model_dump(mode="python"))
    raw = capture["game"]
    provider = _chronology_provider(raw.get("provider"))
    if (
        game.game_id != raw.get("id")
        or _chronology_provider(game.source_id) != provider
        or game.learner_color != raw.get("color")
        or list(game.moves) != json.loads(raw["moves_json"])
        or opening_position_key(game.starting_fen)
        != opening_position_key(raw["start_fen"])
        or game.played_at is None
        or datetime.fromisoformat(game.played_at.replace("Z", "+00:00"))
        != datetime.fromisoformat(raw["played_at"].replace("Z", "+00:00"))
    ):
        raise ValueError("snapshot_comparison_game_mismatch")
    return IntroductionGameScope(
        provider=provider,
        game_id=game.game_id,
        source_revision=game.source_revision,
        source_digest=game.source_digest,
    )


class _PrefixFacts:
    """Detached legal-position facts, bounded and local to one projection."""

    def __init__(self, limit=100001):
        self.limit = limit
        self.roots = {}
        self.edges = {}
        self.facts = {}


def _frames(starting_fen, moves, memo):
    board = chess.Board(starting_fen)
    if not board.is_valid():
        raise ValueError("invalid_source_position")
    # New nodes stay local until the entire route has passed legality checks.
    roots, edges, facts = {}, {}, {}
    next_token = len(memo.facts)
    cacheable = True

    def node(parent, move=None):
        nonlocal next_token, cacheable
        key = starting_fen if parent is None else (parent, move)
        existing = memo.roots.get(key) if parent is None else memo.edges.get(key)
        if existing is not None:
            return existing, memo.facts[existing]
        staged = roots if parent is None else edges
        if key in staged:
            token = staged[key]
            return token, facts[token]
        value = (board.turn, canonical_fen_key(board.fen(en_passant="legal")))
        if not cacheable or next_token >= memo.limit:
            cacheable = False
            return None, value
        token = next_token
        next_token += 1
        staged[key], facts[token] = token, value
        return token, value

    token, first = node(None)
    frames = [first]
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("illegal_source_move")
        board.push(move)
        if token is None:
            frames.append(
                (board.turn, canonical_fen_key(board.fen(en_passant="legal")))
            )
        else:
            token, value = node(token, uci)
            frames.append(value)
    if cacheable:
        memo.roots.update(roots)
        memo.edges.update(edges)
        memo.facts.update(facts)
    return tuple(frames)


def _mappings(
    capture,
    routes,
    revision,
    scope_proof=None,
    comparison_scope=None,
    *,
    include_unavailable=False,
):
    cards = capture["tables"]["cards"]
    counts = Counter(row["id"] for row in cards)
    cards_by_id = {row["id"]: row for row in cards if counts[row["id"]] == 1}
    memberships = {
        (row["repertoire_id"], row["card_id"])
        for row in capture["tables"]["repertoire_cards"]
    }
    mappings = []
    route_positions = {}
    # Every needed authored executable is replayed once in this invocation.
    # The retained values contain detached positions only and share exact route
    # aliases; existing source-row/total-ply bounds cap this retained input.
    replayed = {}
    prefix_facts = _PrefixFacts()
    position_key = lru_cache(maxsize=1024)(opening_position_key)
    card_positions = {}

    def card_position(card):
        # Physical rows are already unique and bounded. Parse each used card's
        # root once, retaining both normalization and validity from that Board.
        if card["id"] not in card_positions:
            board = chess.Board(card["start_fen"])
            card_positions[card["id"]] = (
                canonical_fen_key(board.fen(en_passant="legal")),
                board.is_valid(),
            )
        return card_positions[card["id"]]

    def replay_positions(starting_fen, moves):
        key = (starting_fen, tuple(moves))
        if key not in replayed:
            replayed[key] = _frames(starting_fen, moves, prefix_facts)
        return replayed[key]

    for graph in capture["graphs"]:
        card = cards_by_id.get(graph["card_id"])
        route = routes.get((graph["repertoire_id"], graph["line_id"]))
        if (
            card is None
            or route is None
            or (graph["repertoire_id"], graph["card_id"]) not in memberships
        ):
            continue
        if (
            graph["generation"] != capture["authority"]["graph_generation"]
            or graph["trained_color"] != route.learner_color
            or card.get("trained_color") != route.learner_color
            or card.get("content_type") != "opening"
            or not {"archived", "pending_validation", "superseded_by"} <= card.keys()
        ):
            continue
        available = not any(
            card.get(flag)
            for flag in (
                "archived",
                "pending_validation",
                "superseded_by",
                "integrity_blocked",
                "deleted",
            )
        )
        if not available and not include_unavailable:
            continue
        try:
            executable = json.loads(graph["moves_json"])
            if (
                not executable
                or len(executable) > 1000
                or executable != json.loads(card["moves_json"])
            ):
                continue
            card_key, valid_start = card_position(card)
            graph_key = (
                card_key
                if graph["starting_fen"] == card["start_fen"]
                else position_key(graph["starting_fen"])
            )
            if not valid_start or graph_key != card_key:
                continue
            route_key = (route.repertoire_id, route.route_id)
            if route_key not in route_positions:
                try:
                    occurrences = {}
                    for ply, (_turn, key) in enumerate(
                        replay_positions(route.starting_fen, route.moves)
                    ):
                        occurrences.setdefault(key, []).append(ply)
                    index = MappingProxyType(
                        {key: tuple(plies) for key, plies in occurrences.items()}
                    )
                    route_positions[route_key] = (
                        route,
                        index,
                        replay_positions(route.starting_fen, route.moves),
                    )
                except (ValueError, TypeError, KeyError):
                    route_positions[route_key] = (route, None, None)
            cached_route, positions, route_frames = route_positions[route_key]
            if cached_route is not route or positions is None:
                continue
            roots = [
                ply
                for ply in positions.get(graph_key, ())
                if tuple(route.moves[ply : ply + len(executable)]) == tuple(executable)
            ]
            # Explicit graph membership + exact executable/history is required.
            # Matching FEN/SAN alone never creates a physical binding.
            if len(roots) != 1:
                continue
            # Exact normalized root plus the same ordered executable proves
            # these legal positions. Replaying the physical slice independently
            # cannot add authority; retain its own starting-position validity.
            card_frames = route_frames[roots[0] : roots[0] + len(executable) + 1]
        except (ValueError, TypeError, KeyError):
            continue
        introduction, introduced_at = _introduction(card)
        before = card.get("introduced_before_game")
        known_before = (
            before if type(before) is bool and comparison_scope is not None else None
        )
        if known_before is True:
            introduction = "introduced"
        for offset, uci in enumerate(executable):
            turn, key = card_frames[offset]
            if turn != (route.learner_color == "white"):
                continue
            # The bounded capture may omit an ended/diverged competing route,
            # but its public proof retains the exclusive-policy anchor. Never
            # turn that omission into physical miss authority before the anchor.
            if scope_proof is not None:
                anchor = scope_proof.authority_anchor(route.repertoire_id)
                if anchor is None or roots[0] + offset < anchor:
                    continue
            mapping = DecisionPresentation(
                decision_id=decision_identity(card["id"], offset, executable[:offset]),
                repertoire_id=route.repertoire_id,
                position_key=key,
                selected_response_uci=uci,
                presentation_id=card["id"],
                presentation_revision=card["revision"],
                repertoire_revision=revision,
                introduction=introduction,
                introduced_at=introduced_at,
                route_id=route.route_id,
                route_ply=roots[0] + offset,
                introduced_before_game=known_before,
                introduction_game=comparison_scope
                if known_before is not None
                else None,
                available=available,
                # Retain the donor's original-revision compatibility rule;
                # changed physical content needs explicit owner introduction proof.
                introduction_matches_current_content=(
                    card["introduction_matches_current_content"] is True
                    if "introduction_matches_current_content" in card
                    else card["revision"] == 1
                ),
            )
            mappings.append(mapping)
    # Repeated graph uses of one explicitly mapped decision are not extra cards.
    return tuple({item.model_dump_json(): item for item in mappings}.values())


def _project_repertoire(
    facts: Mapping, *, captured_at: str | None = None, published: bool = False
) -> RepertoireSnapshot:
    """Project the complete bounded repertoire-only owner read, including conflicts.

    No game/clean-integrity prerequisite and no inferred date-only chronology.
    The source digest excludes capture time and every schedule/review/queue field.
    """
    encoded = json.dumps(
        facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    source = json.loads(encoded)
    header = source["repertoire"]
    repertoire_id = header["id"]
    if not header.get("workspace_id"):
        raise ValueError("repertoire_workspace_unknown")
    if json.loads(header.get("canonical_prefix_moves_json", "[]")):
        raise ValueError("canonical_prefix_snapshot_unsupported")
    violations = [
        dict(
            dimension=dimension,
            observed=len(source[name]),
            limit=limit,
            observation="exact",
        )
        for name, dimension, limit in (
            ("lines", "source_rows", 10000),
            ("cards", "card_rows", 10000),
            ("memberships", "membership_rows", 10000),
        )
        if len(source[name]) > limit
    ]
    if violations:
        raise RepertoireSnapshotBoundExceeded(
            stage="projection", sample_complete=True, violations=violations
        )
    if any(
        row["repertoire_id"] != repertoire_id
        for name in ("lines", "memberships", "graphs")
        for row in source[name]
    ):
        raise ValueError("repertoire_snapshot_scope_mismatch")
    routes = tuple(
        AuthoredRoute(
            repertoire_id=repertoire_id,
            route_id=row["id"],
            policy_id=repertoire_id,
            learner_color=row["trained_color"],
            starting_fen=row["start_fen"],
            moves=json.loads(row["moves_json"]),
        )
        for row in sorted(source["lines"], key=lambda row: row["id"])
    )
    authored_moves = sum(len(route.moves) for route in routes)
    if authored_moves > 100000:
        raise RepertoireSnapshotBoundExceeded(
            stage="projection",
            sample_complete=True,
            violations=[
                dict(
                    dimension="authored_moves",
                    observed=authored_moves,
                    limit=100000,
                    observation="exact",
                )
            ],
        )
    colors = {route.learner_color for route in routes}
    if len(colors) != 1:
        raise ValueError(
            "repertoire_color_unknown" if not colors else "repertoire_color_ambiguous"
        )
    source["lines"].sort(key=lambda row: row["id"])
    source["cards"].sort(key=lambda row: row["id"])
    source["memberships"].sort(key=lambda row: (row["repertoire_id"], row["card_id"]))
    source["graphs"].sort(key=lambda row: (row["line_id"], row["decision_index"]))
    if published:
        generation = header.get("graph_generation")
        if (
            header.get("graph_state") != "ready"
            or type(generation) is not int
            or generation < 1
        ):
            raise ValueError("repertoire_publication_unavailable")
        revision = generation
    else:
        revision = header["scope_source_revision"] + 1
    kind = (
        "current_published_repertoire_snapshot"
        if published
        else "current_repertoire_snapshot"
    )
    digest = fingerprint({"kind": kind, "schema_version": 1, "facts": source})
    moment = captured_at or datetime.now(UTC).isoformat()
    membership = MembershipSnapshot(
        workspace_id=header["workspace_id"],
        snapshot_id=("publication:" if published else "repertoire:") + digest,
        revision=revision,
        digest=digest,
        source_revision=digest,
        captured_at=moment,
        scope_id=("publication:" if published else "repertoire:") + repertoire_id,
        scope_repertoire_ids=[repertoire_id],
        complete=True,
        routes=list(routes),
    )
    # Retain the existing exact graph/executable/card mapping law. A missing or
    # non-ready graph leaves physical mapping unknown but never hides source routes.
    mapping_source = {
        "tables": {"cards": source["cards"], "repertoire_cards": source["memberships"]},
        "graphs": source["graphs"] if header.get("graph_state") == "ready" else [],
        "authority": {"graph_generation": header.get("graph_generation")},
    }
    return RepertoireSnapshot(
        membership=membership,
        variant="standard",
        learner_color=next(iter(colors)),
        boundaries=tuple(
            RouteBoundary(
                route_id=route.route_id, terminal_ply=len(route.moves), deliberate=True
            )
            for route in routes
        ),
        decision_presentations=_mappings(
            mapping_source,
            {(route.repertoire_id, route.route_id): route for route in routes},
            revision,
            include_unavailable=True,
        ),
    )


def project_repertoire(
    facts: Mapping, *, captured_at: str | None = None
) -> RepertoireSnapshot:
    """Audit source view: positive authored counter, independent of publication."""
    return _project_repertoire(facts, captured_at=captured_at)


def project_published_repertoire(
    facts: Mapping, *, captured_at: str | None = None
) -> RepertoireSnapshot:
    """Feedback comparison view: explicit ready graph generation, never a guess.

    Current source facts remain in the freshness digest. This is detector input,
    not mutation authority; Study still captures/fences its current owner state.
    """
    return _project_repertoire(facts, captured_at=captured_at, published=True)


@dataclass(frozen=True, init=False)
class PreparedPublishedRepertoire:
    """One Repertoire-owned projection of detached complete publication facts.

    Construction accepts source facts only. Consumers cannot supply a snapshot
    or a readiness assertion in place of the owning projection. This ephemeral
    value grants no current database authority; receiving owners still select
    their policy and fence every prepared publication in their writer.
    """

    _source_json: str
    snapshot: RepertoireSnapshot

    def __init__(self, facts: Mapping, *, captured_at: str | None = None):
        encoded = json.dumps(
            facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        snapshot = project_published_repertoire(
            json.loads(encoded), captured_at=captured_at
        )
        object.__setattr__(self, "_source_json", encoded)
        object.__setattr__(self, "snapshot", snapshot)

    def publication_facts(self) -> dict:
        """Return a detached complete read, never the capability's owned bytes."""
        return json.loads(self._source_json)
