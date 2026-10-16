"""Source identities and complete, immutable position input regressions."""

import copy
import json
from pathlib import Path

import chess
import pytest
from pydantic import ValidationError

from tempo_intelligence.identity import canonical_json, canonical_position, digest, source_descriptor
from tempo_intelligence.models import LichessSource, MaiaSource, MastersSource, RequestInput, Snapshot


def fixture(name):
    return json.loads((Path(__file__).parent.parent / "fixtures" / name).read_text())


def test_complete_position_fixture_preserves_all_twenty_legal_moves():
    snapshot = Snapshot.model_validate(fixture("position-v1.json"))
    assert snapshot.complete and len(snapshot.legal_moves) == 20
    assert set(snapshot.legal_moves) == {move.uci() for move in chess.Board(snapshot.fen).legal_moves}


@pytest.mark.parametrize("change,reason", [
    ({"complete": False}, "incomplete_legal_universe"),
    ({"legal_moves": ["e2e4", "d2d4"]}, "legal_universe_mismatch"),
    ({"legal_universe_digest": "sha256:" + "f" * 64}, "legal_universe_digest_mismatch"),
    ({"position_key": "different-position"}, "position_key_mismatch"),
    ({"fen": "invalid"}, "invalid_position"),
    ({"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}, "invalid_position"),
])
def test_snapshot_rejects_unverified_legal_universe(change, reason):
    payload = fixture("position-v1.json")
    payload.update(change)
    with pytest.raises(ValidationError, match=reason):
        Snapshot.model_validate(payload)


def test_snapshot_rejects_duplicate_or_illegal_moves():
    payload = fixture("position-v1.json")
    payload["legal_moves"].append(payload["legal_moves"][0])
    with pytest.raises(ValidationError, match="legal_universe_mismatch"):
        Snapshot.model_validate(payload)
    payload["legal_moves"][-1] = "e2e5"
    with pytest.raises(ValidationError, match="legal_universe_mismatch"):
        Snapshot.model_validate(payload)


def test_terminal_snapshot_has_no_nonterminal_distribution():
    snapshot = Snapshot.model_validate(fixture("terminal-position-v1.json"))
    assert snapshot.legal_moves == []
    assert chess.Board(snapshot.fen).is_checkmate()
    assert source_descriptor(snapshot, MastersSource(kind="masters"))["parameters"]["moves"] == 0


@pytest.mark.parametrize("name,reason", [
    ("insufficient-material-position-v1.json", "insufficient_material"),
    ("seventyfive-move-position-v1.json", "seventyfive_moves"),
])
def test_terminal_draw_snapshot_preserves_legal_moves_without_querying(name, reason):
    snapshot = Snapshot.model_validate(fixture(name))
    assert snapshot.legal_moves
    descriptor = source_descriptor(snapshot, MastersSource(kind="masters"))
    assert descriptor["legal_moves"] == sorted(snapshot.legal_moves)
    assert descriptor["terminal_reason"] == reason


def test_automatic_draw_input_is_distinct_from_ordinary_clock_canonicalization():
    drawn = fixture("seventyfive-move-position-v1.json")
    ordinary = {**drawn, "snapshot_id": "before-automatic-draw", "fen": drawn["fen"].replace("150 80", "100 80")}
    earlier = {**ordinary, "snapshot_id": "earlier-position", "fen": ordinary["fen"].replace("100 80", "0 1")}
    source = MastersSource(kind="masters")
    descriptors = [source_descriptor(Snapshot.model_validate(value), source) for value in (drawn, ordinary, earlier)]
    assert descriptors[0]["canonical_fen"] == descriptors[1]["canonical_fen"] == descriptors[2]["canonical_fen"]
    assert descriptors[0]["terminal_reason"] == "seventyfive_moves"
    assert descriptors[1]["terminal_reason"] is None
    assert descriptors[1] == descriptors[2]
    assert digest(descriptors[0]) != digest(descriptors[1])


@pytest.mark.parametrize("schema_version", [True, "1", 2, None])
def test_local_input_requires_explicit_integer_schema_one(schema_version):
    payload = fixture("request-v1.json")
    payload["schema_version"] = schema_version
    with pytest.raises(ValidationError):
        RequestInput.model_validate(payload)


def test_request_rejects_unknown_fields_and_duplicate_sources():
    payload = fixture("request-v1.json")
    payload["silent_fallback"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RequestInput.model_validate(payload)
    del payload["silent_fallback"]
    payload["sources"] = [payload["sources"][0], copy.deepcopy(payload["sources"][0])]
    with pytest.raises(ValidationError, match="duplicate_source"):
        RequestInput.model_validate(payload)


def test_one_request_has_at_most_one_configuration_per_source_family():
    payload = fixture("request-v1.json")
    second_cohort = {**payload["sources"][0], "ratings": [2000]}
    payload["sources"] = [payload["sources"][0], second_cohort]
    with pytest.raises(ValidationError, match="duplicate_source"):
        RequestInput.model_validate(payload)


def test_canonical_position_reuses_transpositions_and_normalizes_clocks():
    first, second = chess.Board(), chess.Board()
    for move in ["g1f3", "g8f6", "b1c3", "b8c6"]:
        first.push_uci(move)
    for move in ["b1c3", "b8c6", "g1f3", "g8f6"]:
        second.push_uci(move)
    assert canonical_position(first.fen()) == canonical_position(second.fen())
    key, fen = canonical_position(first.fen())
    assert fen == key + " 0 1"


def test_canonical_position_normalizes_only_nonlegal_en_passant():
    board = chess.Board()
    board.push_uci("e2e4")
    assert " e3 " in board.fen(en_passant="fen")
    assert canonical_position(board.fen(en_passant="fen")) == canonical_position(board.fen(en_passant="legal"))
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    assert canonical_position(board.fen())[0].endswith(" d6")


def test_source_cache_inputs_ignore_policy_and_repertoire_identity():
    first = fixture("position-v1.json")
    second = copy.deepcopy(first)
    second.update({"snapshot_id": "other-snapshot", "source_revision": 2, "publication_id": "another-publication", "publication_generation": 2, "policy_context": {"policy_id": "other-policy", "repertoire_id": "other-repertoire"}})
    source = LichessSource(kind="lichess", ratings=[1600], speeds=["blitz"])
    assert source_descriptor(Snapshot.model_validate(first), source) == source_descriptor(Snapshot.model_validate(second), source)


def test_cohort_sets_have_one_canonical_parameter_order():
    snapshot = Snapshot.model_validate(fixture("position-v1.json"))
    first = LichessSource(kind="lichess", ratings=[1800, 1600], speeds=["rapid", "blitz"])
    second = LichessSource(kind="lichess", ratings=[1600, 1800], speeds=["blitz", "rapid"])
    assert source_descriptor(snapshot, first) == source_descriptor(snapshot, second)
    assert source_descriptor(snapshot, first)["parameters"]["ratings"] == "1600,1800"


def test_distinct_provider_inputs_do_not_share_evidence_identity():
    snapshot = Snapshot.model_validate(fixture("position-v1.json"))
    sources = [
        LichessSource(kind="lichess", ratings=[1600], speeds=["blitz"]),
        LichessSource(kind="lichess", ratings=[1800], speeds=["blitz"]),
        LichessSource(kind="lichess", ratings=[1600], speeds=["rapid"]),
        LichessSource(kind="lichess", ratings=[1600], speeds=["blitz"], since="2020-01"),
        MastersSource(kind="masters"),
    ]
    assert len({digest(source_descriptor(snapshot, source)) for source in sources}) == len(sources)
    descriptor = source_descriptor(snapshot, sources[0])
    changed_schema = {**descriptor, "schema_version": 2}
    assert digest(descriptor) != digest(changed_schema)


@pytest.mark.parametrize("fields,reason", [
    ({"ratings": [400]}, "unsupported_rating_cohort"),
    ({"ratings": [1500]}, "unsupported_rating_cohort"),
    ({"ratings": [1600, 1600]}, "unsupported_rating_cohort"),
    ({"speeds": ["standard"]}, "unsupported_speed_cohort"),
    ({"speeds": ["blitz", "blitz"]}, "unsupported_speed_cohort"),
    ({"since": "2020-13"}, "unsupported_date_range"),
    ({"since": "2021-01", "until": "2020-12"}, "unsupported_date_range"),
])
def test_unsupported_lichess_precision_is_rejected(fields, reason):
    with pytest.raises(ValidationError, match=reason):
        LichessSource(kind="lichess", **fields)


def test_masters_cannot_claim_rating_or_speed_conditioning():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MastersSource.model_validate({"kind": "masters", "ratings": [1600]})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MastersSource.model_validate({"kind": "masters", "speeds": ["blitz"]})


def test_maia_accepts_only_existing_single_rating_context():
    source = fixture("request-v1.json")["sources"][2]
    assert MaiaSource.model_validate(source).rating == 1500
    for unsupported in ({"ratings": [1500, 1800]}, {"speeds": ["blitz"]}):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            MaiaSource.model_validate({**source, **unsupported})


def test_canonical_hash_is_stable_and_rejects_nonfinite_payloads():
    assert canonical_json({"b": 2, "a": [0, None]}) == '{"a":[0,null],"b":2}'
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest(["a", "b"]) != digest(["b", "a"])
    with pytest.raises(ValueError, match="invalid_canonical_payload"):
        canonical_json({"bad": float("nan")})
