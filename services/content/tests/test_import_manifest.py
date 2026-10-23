"""Fresh authored manifests preserve complete routes and explicit identity."""

import hashlib
import json

import chess
import pytest

from tempo_content.bridge import ImportManifest, digest, map_import
from tempo_content.domain import ContentError
from tempo_content.import_manifest import make_manifest


def manifest_for(raw, **overrides):
    return make_manifest(
        raw,
        **{
            "workspace_id": "manifest-test",
            "source_key": "opening",
            "title": "Authored opening",
            "learner_color": "white",
            **overrides,
        },
    )


def identity_sets(manifest):
    return (
        {item.key for item in manifest.repertoires},
        {item.key for item in manifest.policies},
        {(item.fingerprint, item.route_key, item.policy_key) for item in manifest.route_assignments},
    )


@pytest.mark.parametrize("learner_color,expected_moves", [("white", ["e2e4", "g1f3"]), ("black", ["e7e5"])])
def test_fresh_manifest_uses_explicit_identity_color_and_exact_provenance(learner_color, expected_moves):
    raw = b'\xef\xbb\xbf[Event "A header is not an identity"]\n\n1. e4 e5 2. Nf3 *\n'
    manifest = manifest_for(raw, learner_color=learner_color)
    repertoire_key = digest(["repertoire", "manifest-test", "opening"])
    policy_key = digest(["policy", "manifest-test", "opening", learner_color])
    fingerprint = digest([chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3"]])

    assert isinstance(manifest, ImportManifest)
    assert manifest.schema_version == 1
    assert manifest.kind == "authored_import"
    assert manifest.workspace_id == "manifest-test"
    assert manifest.source_key == "opening"
    assert manifest.title == "Authored opening"
    assert manifest.complete is True
    assert manifest.provenance.model_dump() == {
        "kind": "authored_export",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "description": "Authored opening",
        "evidence_refs": [],
    }
    assert [item.model_dump() for item in manifest.repertoires] == [
        {"key": repertoire_key, "title": "Authored opening", "learner_color": learner_color}
    ]
    assert [item.model_dump() for item in manifest.policies] == [
        {"key": policy_key, "repertoire_key": repertoire_key}
    ]
    assert [item.model_dump() for item in manifest.route_assignments] == [{
        "fingerprint": fingerprint,
        "route_key": digest(["route", "opening", fingerprint]),
        "policy_key": policy_key,
    }]
    assert manifest.legacy_aliases == {}
    assert manifest.historical_mapping_unknown == []
    assert manifest.reentry == []

    source, accepted = map_import(raw, manifest.model_dump(mode="json"))
    assert accepted == manifest
    assert source.repertoires == manifest.repertoires
    assert source.policies == manifest.policies
    assert sorted(exercise.specification.accepted_lines[0][0] for exercise in source.exercises) == sorted(expected_moves)


def test_all_authored_variations_become_sorted_complete_route_assignments():
    raw = b"1. e4 (1. d4 d5 2. c4) e5 (1... c5 2. Nf3 (2. Nc3)) 2. Nf3 *\n"
    paths = [
        ["e2e4", "e7e5", "g1f3"],
        ["d2d4", "d7d5", "c2c4"],
        ["e2e4", "c7c5", "g1f3"],
        ["e2e4", "c7c5", "b1c3"],
    ]
    manifest = manifest_for(raw)
    assert [item.fingerprint for item in manifest.route_assignments] == sorted(
        digest([chess.STARTING_FEN, moves]) for moves in paths
    )
    source, _ = map_import(raw, manifest.model_dump(mode="json"))
    assert {tuple(route.moves) for route in source.routes} == {tuple(path) for path in paths}
    assert len(source.memberships) == 4


def test_reordering_duplicates_headers_comments_and_title_preserve_identity_sets():
    original = b'[Event "First"]\n\n1. e4 e5 2. Nf3 *\n\n[Event "Second"]\n\n1. e4 c5 2. Nf3 *\n'
    reordered = b'[Event "Renamed"]\n\n1. e4 {Authored comment} c5 2. Nf3 *\n\n1. e4 e5 2. Nf3 *\n\n1. e4 e5 2. Nf3 *\n'
    first = manifest_for(original)
    second = manifest_for(reordered, title="Renamed repertoire")
    assert identity_sets(first) == identity_sets(second)
    assert first.route_assignments == second.route_assignments
    assert len(first.route_assignments) == len(second.route_assignments) == 2
    assert first.provenance.source_sha256 != second.provenance.source_sha256
    assert second.provenance.source_sha256 == hashlib.sha256(reordered).hexdigest()
    assert second.provenance.description == second.title == second.repertoires[0].title == "Renamed repertoire"
    source, _ = map_import(original, first.model_dump(mode="json"))
    successor, _ = map_import(reordered, second.model_dump(mode="json"))
    assert {exercise.key for exercise in source.exercises} == {exercise.key for exercise in successor.exercises}
    assert {route.key for route in source.routes} == {route.key for route in successor.routes}


def test_variation_order_does_not_change_assignments():
    first = manifest_for(b"1. e4 (1. d4 d5) e5 *\n")
    second = manifest_for(b"1. d4 (1. e4 e5) d5 *\n")
    assert first.route_assignments == second.route_assignments
    assert first.provenance.source_sha256 != second.provenance.source_sha256


def test_identity_changes_only_follow_the_supplied_identity_inputs():
    raw = b"1. e4 e5 *\n"
    white = manifest_for(raw)
    black = manifest_for(raw, learner_color="black")
    assert white.repertoires[0].key == black.repertoires[0].key
    assert white.policies[0].key != black.policies[0].key
    assert white.route_assignments[0].route_key == black.route_assignments[0].route_key
    for overrides in ({"workspace_id": "other-workspace"}, {"source_key": "other-source"}):
        other = manifest_for(raw, **overrides)
        assert white.repertoires[0].key != other.repertoires[0].key
        assert white.policies[0].key != other.policies[0].key
        assert white.route_assignments[0].fingerprint == other.route_assignments[0].fingerprint
        assert (white.route_assignments[0].route_key == other.route_assignments[0].route_key) == ("source_key" not in overrides)


def test_fen_start_including_exact_en_passant_state_is_part_of_route_identity():
    starts = [
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1",
    ]
    raw = "\n\n".join(
        f'[Variant "From Position"]\n[SetUp "1"]\n[FEN "{fen}"]\n\n1... c5 2. Nf3 *'
        for fen in starts
    ).encode()
    manifest = manifest_for(raw, learner_color="black")
    assert [item.fingerprint for item in manifest.route_assignments] == sorted(
        digest([fen, ["c7c5", "g1f3"]]) for fen in starts
    )
    assert len({item.route_key for item in manifest.route_assignments}) == 2
    source, _ = map_import(raw, manifest.model_dump(mode="json"))
    assert {route.starting_fen for route in source.routes} == set(starts)


@pytest.mark.parametrize("raw", [
    b"{An authored comment}\n1. e4 e5 2. Nf3 *\n",
    b" \n\t{An authored comment}\n1. e4 e5 2. Nf3 *\n",
])
def test_leading_brace_comment_pgn_keeps_raw_digest_and_maps_with_its_manifest(raw):
    manifest = manifest_for(raw)

    assert manifest.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    source, _ = map_import(raw, manifest.model_dump(mode="json"))
    assert len(source.routes) == 1
    assert source.routes[0].moves == ["e2e4", "e7e5", "g1f3"]


def test_structured_json_remains_strict_and_uses_the_exact_raw_digest():
    pgn = b"1. e4 e5 2. Nf3 *\n"
    manifest = manifest_for(pgn)
    source, _ = map_import(pgn, manifest.model_dump(mode="json"))
    structured = source.model_dump_json().encode()
    matching = manifest.model_copy(update={
        "provenance": manifest.provenance.model_copy(update={
            "source_sha256": hashlib.sha256(structured).hexdigest(),
        }),
    })

    decoded, _ = map_import(structured, matching.model_dump(mode="json"))
    assert decoded == source
    with pytest.raises(ValueError):
        map_import(structured, manifest.model_dump(mode="json"))

    malformed_source = json.dumps({"schema_version": 1, "kind": "unsupported"}).encode()
    invalid_schema = manifest.model_copy(update={
        "provenance": manifest.provenance.model_copy(update={
            "source_sha256": hashlib.sha256(malformed_source).hexdigest(),
        }),
    })
    with pytest.raises(ValueError):
        map_import(malformed_source, invalid_schema.model_dump(mode="json"))


@pytest.mark.parametrize("raw", [
    b"", b" \n\t", b"This is not a PGN", b"{A comment only}", b'[Event "No moves"]\n\n*',
    b"1. e4 e5 2. Bh6 *", b"1. e4 (1. d4 d5 2. Bh5) e5 *", b"1. -- *", b"\xff\xfe",
    b'1. e4 *\n\n[Event "Empty game"]\n\n*',
])
def test_empty_invalid_or_illegal_pgn_is_rejected(raw):
    with pytest.raises(ContentError) as caught:
        manifest_for(raw)
    assert caught.value.code == "invalid_pgn"


@pytest.mark.parametrize("variant", ["Chess960", "Atomic", "Unknown variant"])
def test_unsupported_variants_are_rejected(variant):
    with pytest.raises(ContentError) as caught:
        manifest_for(f'[Variant "{variant}"]\n\n1. e4 *\n'.encode())
    assert caught.value.code == "invalid_pgn"


@pytest.mark.parametrize("fen", ["not a FEN", "8/8/8/8/8/8/4P3/8 w - - 0 1"])
def test_malformed_and_structurally_invalid_starting_boards_are_rejected(fen):
    with pytest.raises(ContentError) as caught:
        manifest_for(f'[SetUp "1"]\n[FEN "{fen}"]\n\n1. e4 *\n'.encode())
    assert caught.value.code in {"invalid_pgn", "invalid_fen"}


def test_input_larger_than_sixteen_mib_is_rejected():
    with pytest.raises(ContentError) as caught:
        manifest_for(b" " * (16 * 1024 * 1024 + 1))
    assert caught.value.code == "source_bound"


@pytest.mark.parametrize("plies", [1000, 1001])
def test_complete_route_ply_limit_is_inclusive_and_never_truncates(plies):
    raw = (" ".join(["Nf3", "Nf6", "Ng1", "Ng8"][ply % 4] for ply in range(plies)) + " *\n").encode()
    if plies > 1000:
        with pytest.raises(ContentError) as caught:
            manifest_for(raw)
        assert caught.value.code == "route_bound"
    else:
        manifest = manifest_for(raw)
        source, _ = map_import(raw, manifest.model_dump(mode="json"))
        assert len(source.routes[0].moves) == plies


@pytest.mark.parametrize("paths", [10000, 10001])
def test_total_complete_path_limit_counts_duplicate_games_across_the_whole_input(paths):
    raw = b"1. e4 *\n\n" * paths
    if paths > 10000:
        with pytest.raises(ContentError) as caught:
            manifest_for(raw)
        assert caught.value.code == "route_count_bound"
    else:
        manifest = manifest_for(raw)
        assert len(manifest.route_assignments) == 1


def test_complete_path_limit_also_bounds_the_variation_frontier():
    raw = b"1. e4 " + b"(1. d4) " * 10000 + b"*\n"
    with pytest.raises(ContentError) as caught:
        manifest_for(raw)
    assert caught.value.code == "route_count_bound"


def test_existing_map_import_validation_must_succeed_before_return(monkeypatch):
    import tempo_content.import_manifest as module

    calls = []

    def validate(raw, manifest):
        calls.append((raw, manifest))
        return map_import(raw, manifest)

    monkeypatch.setattr(module, "map_import", validate)
    raw = b"1. e4 e5 *\n"
    manifest = manifest_for(raw)
    assert calls == [(raw, manifest.model_dump(mode="json"))]

    def reject(raw, manifest):
        raise ContentError("source_scope_mismatch", "Existing bridge rejected the source")

    monkeypatch.setattr(module, "map_import", reject)
    with pytest.raises(ContentError, match="Existing bridge rejected"):
        manifest_for(raw)
