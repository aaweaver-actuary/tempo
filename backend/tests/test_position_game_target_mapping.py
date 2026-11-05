"""Pure mapping of durable first-own departures to position identities."""

from dataclasses import dataclass

from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import Hold, PositionDefinition, PositionKey

from app.contracts.product_v02.games import (
    FirstDepartureRoute,
    FirstDepartureScope,
    FirstDepartureSelection,
)
from app.contracts.product_v02.learning import (
    FirstDepartureWitness,
    GameEffectKey,
    GameEncounter,
)
from app.contracts.product_v02.repertoire import (
    DecisionPresentation,
    IntroductionGameScope,
)
from app.study.position_learning.game_target_mapping import map_first_departure
from fixtures.position_identity_examples import (
    LegalPath,
    LONDON_ORDER_A,
    LONDON_ORDER_B,
)

LONDON_DIFFERENT_POSITION = LegalPath(
    "london",
    "london_knight_developed",
    ("d2d4", "d7d5", "c1f4", "g8f6", "g1f3", "e7e6"),
)


@dataclass
class Source:
    result: PositionDefinition | Hold
    calls: list

    def current_definition(self, *, repertoire_id, position):
        self.calls.append((repertoire_id, position))
        return self.result


def departure(
    path,
    *,
    game_id="game",
    repertoire_id=None,
    answer="g1f3",
    policy="london-policy",
    decision_id="decision",
):
    repertoire_id = repertoire_id or path.repertoire_id
    position = path.position(6)
    source = IntroductionGameScope(
        provider="lichess", game_id=game_id, source_revision=1, source_digest="a" * 64
    )
    scope = FirstDepartureScope(
        repertoire_id=repertoire_id,
        learner_color="white",
        snapshot_id="snapshot",
        source_revision="revision-1",
        revision=1,
        digest="b" * 64,
        complete=True,
        available=True,
    )
    decision = DecisionPresentation(
        decision_id=decision_id,
        repertoire_id=repertoire_id,
        position_key=position,
        selected_response_uci=answer,
        presentation_id="card",
        presentation_revision=1,
        repertoire_revision=1,
        introduction="unknown",
        introduced_at=None,
        available=True,
        route_id=path.name,
        route_ply=6,
    )
    route = FirstDepartureRoute(
        repertoire_id=repertoire_id,
        route_id=path.name,
        route_ply=6,
        policy_id=policy,
        source_revision="revision-1",
        at_deliberate_end=False,
    )
    selection = FirstDepartureSelection(
        workspace_id="workspace",
        source=source,
        learner_color="white",
        selected_scope=(scope,),
        status="own_actionable",
        ply=7,
        actor="learner",
        position_key=position,
        played_uci="c2c4",
        expected_uci=(answer,),
        decision=decision,
        surviving_routes=(route,),
        diagnostic=None,
    )
    witness = FirstDepartureWitness(
        selection=selection,
        configuration_revision=4,
        determined_at="2026-10-30T00:00:00Z",
        stream="forward",
    )
    encounter = GameEncounter(
        key=GameEffectKey(
            workspace_id="workspace",
            provider="lichess",
            game_id=game_id,
            decision_id=decision_id,
            occurrence_ply=7,
        ),
        source=source,
        decision=decision,
        expected_uci=answer,
        played_uci="c2c4",
        observation="missed",
        detected_at="2026-10-30T00:00:00Z",
        occurred_at="2026-10-29T00:00:00Z",
        payload_digest="c" * 64,
    )
    return encounter, witness, position


def test_two_london_move_orders_map_to_one_exact_position_target():
    assert LONDON_ORDER_A.position(6) == LONDON_ORDER_B.position(6)
    first, first_witness, position = departure(LONDON_ORDER_A, game_id="game-a")
    second, second_witness, _ = departure(LONDON_ORDER_B, game_id="game-b")
    definition = PositionDefinition(
        PositionKey("london", position), "g1f3", "london-policy"
    )
    source = Source(definition, [])

    one = map_first_departure(first, first_witness, current_source=source)
    two = map_first_departure(second, second_witness, current_source=source)

    assert one.definition == two.definition == definition
    assert one.original_effect_key == first.key
    assert two.original_effect_key == second.key
    assert one.original_effect_key != two.original_effect_key
    assert source.calls == [("london", position), ("london", position)]


def test_same_position_in_another_repertoire_has_a_distinct_identity():
    command, witness, position = departure(LONDON_ORDER_A, repertoire_id="sicilian")
    source = Source(
        PositionDefinition(PositionKey("sicilian", position), "g1f3", "london-policy"),
        [],
    )

    target = map_first_departure(command, witness, current_source=source)

    assert target.definition.key == PositionKey("sicilian", position)
    assert target.definition.key != PositionKey("london", position)


def test_different_exact_position_maps_to_a_distinct_position_key():
    first, first_witness, first_position = departure(LONDON_ORDER_A, game_id="game-a")
    other, other_witness, other_position = departure(
        LONDON_DIFFERENT_POSITION, game_id="game-b", answer="e2e3"
    )
    assert first_position != other_position
    source = Source(
        PositionDefinition(
            PositionKey("london", first_position), "g1f3", "london-policy"
        ),
        [],
    )
    other_source = Source(
        PositionDefinition(
            PositionKey("london", other_position), "e2e3", "london-policy"
        ),
        [],
    )

    one = map_first_departure(first, first_witness, current_source=source)
    two = map_first_departure(other, other_witness, current_source=other_source)

    assert one.definition.key != two.definition.key


def test_current_answer_or_policy_change_holds_without_rewriting_original_key():
    command, witness, position = departure(LONDON_ORDER_A)
    original_key = command.key
    changed_answer = Source(
        PositionDefinition(PositionKey("london", position), "d2d4", "london-policy"), []
    )
    changed_policy = Source(
        PositionDefinition(PositionKey("london", position), "g1f3", "new-policy"), []
    )

    assert map_first_departure(command, witness, current_source=changed_answer) == Hold(
        "current_response_changed"
    )
    assert map_first_departure(command, witness, current_source=changed_policy) == Hold(
        "current_policy_changed"
    )
    assert command.key == original_key


def test_stale_current_source_capability_returns_a_typed_hold():
    command, witness, _ = departure(LONDON_ORDER_A)
    stale = Source(Hold("source_digest_changed"), [])

    result = map_first_departure(command, witness, current_source=stale)

    assert result == Hold("source_digest_changed")
    assert stale.calls


def test_opponent_first_gap_never_requests_a_position_target():
    position = opening_position_key(LONDON_ORDER_A.board_at(1).fen())
    source = IntroductionGameScope(
        provider="lichess", game_id="game", source_revision=1, source_digest="a" * 64
    )
    scope = FirstDepartureScope(
        repertoire_id="london",
        learner_color="white",
        snapshot_id="snapshot",
        source_revision="revision-1",
        revision=1,
        digest="b" * 64,
        complete=True,
        available=True,
    )
    route = FirstDepartureRoute(
        repertoire_id="london",
        route_id="route",
        route_ply=1,
        policy_id="london-policy",
        source_revision="revision-1",
        at_deliberate_end=False,
    )
    gap = FirstDepartureSelection(
        workspace_id="workspace",
        source=source,
        learner_color="white",
        selected_scope=(scope,),
        status="opponent_gap",
        ply=2,
        actor="opponent",
        position_key=position,
        played_uci="c7c5",
        expected_uci=("d7d5",),
        decision=None,
        surviving_routes=(route,),
        diagnostic=None,
    )
    command, _, _ = departure(LONDON_ORDER_A)
    source_port = Source(Hold("must_not_be_called"), [])

    result = map_first_departure(command, gap, current_source=source_port)

    assert result == Hold("opponent_first_gap_not_a_lapse")
    assert source_port.calls == []


def test_retry_keeps_the_original_game_effect_identity_and_aliases_do_not_merge_provenance():
    command, witness, position = departure(LONDON_ORDER_A)
    definition = PositionDefinition(
        PositionKey("london", position), "g1f3", "london-policy"
    )
    source = Source(definition, [])

    original = map_first_departure(command, witness, current_source=source)
    retry = map_first_departure(command, witness, current_source=source)

    assert original == retry
    assert retry.original_effect_key.logical_id() == command.key.logical_id()
    assert retry.source_digest == command.source.source_digest
    assert source.calls == [("london", position), ("london", position)]
