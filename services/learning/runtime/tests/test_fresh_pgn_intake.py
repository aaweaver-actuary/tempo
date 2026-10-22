"""Installed Content intake preserves Learning's ordinary Study authority."""

import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest
from tempo_learning_runtime.frozen_maia_math import content_fingerprint

from test_authoritative_bridge import (
    authored,
    drop_first_activation,
    inspect_rows,
    run_cli,
)
from test_restart import server
from test_runtime import activate, bundle, review_payload


PGN_WITH_VARIATION = (
    '[Event "Task-owned synthetic issue386 opening"]\n\n'
    '1. e4 (1. d4 d5 2. c4) e5 (1... c5 2. Nf3) 2. Nf3 *\n'
)


def preview_manifest(content_cli, environment, directory, *, source_key, title, color, pgn=PGN_WITH_VARIATION):
    source_path = directory / f"{source_key}-{uuid.uuid4().hex}.pgn"
    source_path.write_text(pgn)
    manifest_path = source_path.with_suffix(".manifest.json")
    preview = run_cli(
        content_cli,
        [
            "import", "manifest", source_path,
            "--workspace", "workspace-a",
            "--source-key", source_key,
            "--title", title,
            "--color", color,
            "--output", manifest_path,
        ],
        environment,
        directory,
    )
    assert preview["status"] == "validated"
    assert json.loads(manifest_path.read_text())["repertoires"][0]["learner_color"] == color
    return source_path, manifest_path, preview


def draft_and_approve(content_cli, environment, directory, source_path, manifest_path, expected_revision):
    draft = run_cli(
        content_cli,
        ["import", "plan", source_path, "--manifest", manifest_path,
         "--expected-revision", expected_revision],
        environment,
        directory,
    )
    assert draft["status"] == "draft"
    approved = run_cli(
        content_cli,
        ["draft", "approve", draft["id"], "--digest", draft["digest"]],
        environment,
        directory,
    )
    assert approved["state"] == "approved"
    return draft, approved


def apply_arguments(content_cli, origin, token_path, operation_id):
    return [
        "--content-cli", content_cli,
        "--learning-origin", origin,
        "--operator-token-file", token_path,
        "apply", operation_id,
    ]


def private_token(case, directory):
    path = directory / "operator-token"
    path.write_text(case.configuration.operator_token)
    path.chmod(0o600)
    return path


def study_window(client, required_ids=()):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get("/api/queue/today")
        assert response.status_code in (200, 503), response.text
        if response.status_code == 200:
            cards = response.json()["cards"]
            if cards and (not required_ids or required_ids.intersection(card["id"] for card in cards)):
                return cards
        time.sleep(0.05)
    raise AssertionError("Installed Learning did not issue the required ordinary Study card")


def study_review(client, card):
    operation_id = uuid.uuid4().hex
    response = client.post(
        f"/api/cards/{card['id']}/review",
        json=review_payload(card),
        headers={"Idempotency-Key": operation_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["persisted"] is True
    assert client.get(f"/api/operations/{operation_id}").json()["response"] == response.json()
    return response


def evidence(name, document):
    root = Path(os.environ["TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE"])
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")


def seed_existing_frozen_ranks(case):
    """Reuse the accepted #231 frozen-rank publication contract as fixture input."""
    payload = bundle(
        [
            ("opening", {"type": "move_line", "accepted_lines": [[move]],
                         "mode": "single", "grading_policy": "reference"})
            for move in ("e2e4", "d2d4")
        ]
    )
    ranks = []
    for index, (definition, fact) in enumerate(zip(payload["publication"]["definitions"], payload["facts"])):
        card = {
            "id": definition["card_id"],
            "revision": 1,
            "start_fen": definition["starting_fen"],
            "moves_json": json.dumps(definition["specification"]["accepted_lines"][0]),
            "trained_color": "white",
            "kind": "checkpoint",
            "content_type": "opening",
            "source_ref": None,
            "source_fen": None,
        }
        ranks.append({
            "card_id": card["id"],
            "ordinal": 2 - index,
            "fingerprint": content_fingerprint(card, fact["memberships"]),
        })
    payload["frozen_order"] = {
        "reader_version": "frozen-maia-v2",
        "run_id": "issue386-frozen-run",
        "ranks": ranks,
    }
    activate(case, payload)
    assert inspect_rows(case, "SELECT count(*) FROM learning.frozen_opening_ranks")[0][0] == 2
    return [rank["card_id"] for rank in sorted(ranks, key=lambda item: item["ordinal"])]


@pytest.mark.parametrize("color", ["white", "black"])
def test_fresh_white_black_pgn_variations_apply_and_study_with_frozen_ranks(case, authored, tmp_path, color):
    content_cli, bridge_cli, environment = authored
    frozen_order = seed_existing_frozen_ranks(case)
    source_key = "issue386-fresh-" + color
    source_path, manifest_path, preview = preview_manifest(
        content_cli, environment, tmp_path,
        source_key=source_key,
        title="Synthetic white/black PGN variation " + color,
        color=color,
    )
    assert preview["route_count"] >= 2
    draft, approved = draft_and_approve(
        content_cli, environment, tmp_path, source_path, manifest_path, 0
    )
    assert inspect_rows(case, "SELECT count(*) FROM learning.activation_receipts")[0][0] == 1

    token_path = private_token(case, tmp_path)
    with server(case, tmp_path) as (client, _process):
        completed = run_cli(
            bridge_cli,
            apply_arguments(content_cli, str(client.base_url).rstrip("/"), token_path, approved["id"]),
            environment,
            tmp_path,
        )
        assert completed["state"] == "completed"
        publication = completed["document"]["export"]["publication"]
        fresh_ids = {item["card_id"] for item in publication["definitions"]}
        assert len(fresh_ids) >= 1
        fresh_card_colors = inspect_rows(
            case,
            "SELECT id,trained_color FROM learning.cards WHERE id = ANY(%s) AND serving_active = 1 ORDER BY id",
            (list(fresh_ids),),
        )
        assert dict(fresh_card_colors) == {card_id: color for card_id in fresh_ids}
        assert inspect_rows(
            case,
            "SELECT count(*) FROM learning.frozen_opening_ranks WHERE run_id = %s",
            ("issue386-frozen-run",),
        )[0][0] == 2
        assert inspect_rows(
            case,
            "SELECT count(*) FROM learning.frozen_opening_ranks WHERE run_id = %s AND card_id = ANY(%s)",
            ("issue386-frozen-run", list(fresh_ids)),
        )[0][0] == 0

        # All Content/operator CLI processes have returned. Only Learning HTTP
        # remains in use while the existing frozen ranks and fresh card are served.
        cards = study_window(client, required_ids=fresh_ids)
        assert all(
            card["trained_color"] == color
            for card in cards
            if card["id"] in fresh_ids
        )
        positions = {card["id"]: index for index, card in enumerate(cards)}
        admission_facts = {
            "card_rows": inspect_rows(
                case,
                "SELECT id,state,due_date,serving_active,pending_validation,introduced_at,publication_generation FROM learning.cards WHERE id = ANY(%s) ORDER BY id",
                (list(fresh_ids),),
            ),
            "route_rows": inspect_rows(
                case,
                "SELECT card_id,repertoire_id,generation,parent_card_id,decision_index FROM learning.opening_graph_steps WHERE card_id = ANY(%s) ORDER BY card_id,repertoire_id,generation,decision_index",
                (list(fresh_ids),),
            ),
            "queue_rows": inspect_rows(
                case,
                "SELECT card_id,queue_date,status,position,admission_kind FROM learning.daily_queue WHERE card_id = ANY(%s) ORDER BY queue_date,position,id",
                (list(fresh_ids),),
            ),
            "study_queue_ids": [card["id"] for card in cards],
            "fresh_card_ids": sorted(fresh_ids),
            "frozen_rank_card_ids": frozen_order,
        }
        evidence(f"fresh-intake-admission-{color}.json", admission_facts)
        assert all(card_id in positions for card_id in frozen_order)
        assert fresh_ids.intersection(positions), admission_facts
        assert [positions[card_id] for card_id in frozen_order] == sorted(
            positions[card_id] for card_id in frozen_order
        )
        assert min(positions[card_id] for card_id in fresh_ids.intersection(positions)) > max(
            positions[card_id] for card_id in frozen_order
        )
        for expected_id in frozen_order:
            head = study_window(client)[0]
            assert head["id"] == expected_id
            study_review(client, head)
        fresh_head = study_window(client)[0]
        assert fresh_head["id"] in fresh_ids
        assert fresh_head["trained_color"] == color
        study_review(client, fresh_head)

        evidence(
            f"fresh-pgn-{color}.json",
            {
                "synthetic": True,
                "color": color,
                "source_key": source_key,
                "source_sha256": json.loads(manifest_path.read_text())["provenance"]["source_sha256"],
                "draft_id": draft["id"],
                "operation_id": approved["id"],
                "publication_id": publication["publication_id"],
                "publication_generation": publication["publication_generation"],
                "fresh_card_ids": sorted(fresh_ids),
                "frozen_rank_card_ids": frozen_order,
                "study_reviewed_ids": [*frozen_order, fresh_head["id"]],
            },
        )


def test_approved_delivery_replay_after_lost_response_has_one_publication_and_effect(case, authored, tmp_path):
    content_cli, bridge_cli, environment = authored
    source_key = "issue386-lost-response"
    source_path, manifest_path, _ = preview_manifest(
        content_cli, environment, tmp_path,
        source_key=source_key,
        title="Synthetic approved delivery replay",
        color="white",
    )
    _, approved = draft_and_approve(content_cli, environment, tmp_path, source_path, manifest_path, 0)
    token_path = private_token(case, tmp_path)
    with server(case, tmp_path) as (client, _process), drop_first_activation(
        str(client.base_url).rstrip("/")
    ) as (origin, dropped):
        arguments = apply_arguments(content_cli, origin, token_path, approved["id"])
        lost = run_cli(bridge_cli, arguments, environment, tmp_path, success=False)
        assert isinstance(lost, subprocess.CompletedProcess)
        assert lost.returncode != 0 and len(dropped) == 1
        remote_operation_id = approved["id"] + ":activate"
        assert dropped[0]["activation_id"] == remote_operation_id
        prepared = run_cli(content_cli, ["operation", "show", approved["id"]], environment, tmp_path)
        expected_card_count = len(prepared["document"]["export"]["publication"]["definitions"])
        before_replay = {
            "receipts": inspect_rows(
                case,
                "SELECT count(*) FROM learning.activation_receipts WHERE operation_id = %s",
                (remote_operation_id,),
            )[0][0],
            "accepted": inspect_rows(
                case,
                "SELECT count(*) FROM learning.accepted_publications WHERE publication_id = %s",
                (dropped[0]["publication_id"],),
            )[0][0],
            "cards": inspect_rows(
                case,
                "SELECT count(*) FROM learning.cards WHERE publication_id = %s AND serving_active = 1",
                (dropped[0]["publication_id"],),
            )[0][0],
        }
        assert before_replay == {"receipts": 1, "accepted": 1, "cards": expected_card_count}

        recovered = run_cli(bridge_cli, arguments, environment, tmp_path)
        replayed = run_cli(bridge_cli, arguments, environment, tmp_path)
        assert recovered["state"] == replayed["state"] == "completed"
        assert recovered == replayed
        after_replay = {
            "receipts": inspect_rows(
                case,
                "SELECT count(*) FROM learning.activation_receipts WHERE operation_id = %s",
                (remote_operation_id,),
            )[0][0],
            "accepted": inspect_rows(
                case,
                "SELECT count(*) FROM learning.accepted_publications WHERE publication_id = %s",
                (dropped[0]["publication_id"],),
            )[0][0],
            "cards": inspect_rows(
                case,
                "SELECT count(*) FROM learning.cards WHERE publication_id = %s AND serving_active = 1",
                (dropped[0]["publication_id"],),
            )[0][0],
        }
        assert after_replay == before_replay
        evidence(
            "lost-response-replay.json",
            {
                "synthetic": True,
                "source_sha256": json.loads(manifest_path.read_text())["provenance"]["source_sha256"],
                "operation_id": approved["id"],
                "learning_operation_id": remote_operation_id,
                "publication_id": dropped[0]["publication_id"],
                "counts_before_replay": before_replay,
                "counts_after_replay": after_replay,
                "expected_accepted_card_count": expected_card_count,
                "lost_response_observed": True,
                "same_operation_replay_completed": True,
            },
        )


def test_same_identity_unchanged_decision_reimport_preserves_review_fsrs_and_card_identity(case, authored, tmp_path):
    content_cli, bridge_cli, environment = authored
    source_key = "issue386-preserve-review"
    source_path, manifest_path, _ = preview_manifest(
        content_cli, environment, tmp_path,
        source_key=source_key,
        title="Synthetic preserved review",
        color="white",
    )
    _, first_approval = draft_and_approve(content_cli, environment, tmp_path, source_path, manifest_path, 0)
    token_path = private_token(case, tmp_path)
    with server(case, tmp_path) as (client, _process):
        first = run_cli(
            bridge_cli,
            apply_arguments(content_cli, str(client.base_url).rstrip("/"), token_path, first_approval["id"]),
            environment,
            tmp_path,
        )
        assert first["state"] == "completed"
        first_ids = {item["card_id"] for item in first["document"]["export"]["publication"]["definitions"]}
        card = study_window(client)[0]
        assert card["id"] in first_ids
        study_review(client, card)
        card_id = card["id"]
        before = inspect_rows(
            case,
            "SELECT state,due_date,fsrs_card_json,repetitions,interval_days,ease,lapses,stability,hard_correct_streak FROM learning.cards WHERE id = %s",
            (card_id,),
        )
        history = inspect_rows(
            case,
            "SELECT * FROM learning.reviews WHERE card_id = %s ORDER BY id",
            (card_id,),
        )
        assert history and before[0][2] is not None

        # A title change makes a new explicit Content revision while route and
        # decision identities remain unchanged.
        second_source_path, second_manifest_path, _ = preview_manifest(
            content_cli, environment, tmp_path,
            source_key=source_key,
            title="Synthetic preserved review, renamed source",
            color="white",
        )
        current = run_cli(content_cli, ["source", "show", source_key], environment, tmp_path)
        _, second_approval = draft_and_approve(
            content_cli, environment, tmp_path,
            second_source_path, second_manifest_path, current["requested_revision"],
        )
        assert second_approval["id"] != first_approval["id"]
        second = run_cli(
            bridge_cli,
            apply_arguments(content_cli, str(client.base_url).rstrip("/"), token_path, second_approval["id"]),
            environment,
            tmp_path,
        )
        assert second["state"] == "completed"
        second_ids = {item["card_id"] for item in second["document"]["export"]["publication"]["definitions"]}
        assert second_ids == first_ids
        after = inspect_rows(
            case,
            "SELECT state,due_date,fsrs_card_json,repetitions,interval_days,ease,lapses,stability,hard_correct_streak FROM learning.cards WHERE id = %s",
            (card_id,),
        )
        assert inspect_rows(case, "SELECT id FROM learning.cards WHERE id = %s", (card_id,)) == [(card_id,)]
        assert after == before
        assert inspect_rows(
            case,
            "SELECT * FROM learning.reviews WHERE card_id = %s ORDER BY id",
            (card_id,),
        ) == history
        evidence(
            "preserved-review-reimport.json",
            {
                "synthetic": True,
                "source_sha256_before": json.loads(manifest_path.read_text())["provenance"]["source_sha256"],
                "source_sha256_after": json.loads(second_manifest_path.read_text())["provenance"]["source_sha256"],
                "first_operation_id": first_approval["id"],
                "second_operation_id": second_approval["id"],
                "first_publication_id": first["document"]["export"]["publication"]["publication_id"],
                "second_publication_id": second["document"]["export"]["publication"]["publication_id"],
                "preserved_card_id": card_id,
                "preserved_review_count": len(history),
                "fsrs_schedule_preserved": True,
                "decision_card_ids_unchanged": True,
            },
        )


def test_invalid_pgn_and_stale_revision_fail_before_activation_and_keep_previous_content(case, authored, tmp_path):
    content_cli, bridge_cli, environment = authored
    source_key = "issue386-prior-content"
    source_path, manifest_path, _ = preview_manifest(
        content_cli, environment, tmp_path,
        source_key=source_key,
        title="Synthetic prior active content",
        color="white",
    )
    _, approved = draft_and_approve(content_cli, environment, tmp_path, source_path, manifest_path, 0)
    token_path = private_token(case, tmp_path)
    with server(case, tmp_path) as (client, _process):
        active = run_cli(
            bridge_cli,
            apply_arguments(content_cli, str(client.base_url).rstrip("/"), token_path, approved["id"]),
            environment,
            tmp_path,
        )
        assert active["state"] == "completed"
        publication = active["document"]["export"]["publication"]
        headers = {"Authorization": "Bearer " + case.configuration.operator_token}
        status_path = f"/internal/publications/{publication['publication_id']}/status"
        prior_status = client.get(status_path, headers=headers)
        assert prior_status.status_code == 200
        prior_facts = {
            key: prior_status.json()[key]
            for key in ("status", "publication_id", "publication_generation", "activation_revision", "local_date")
        }
        active_cards = inspect_rows(
            case,
            "SELECT id,serving_active FROM learning.cards WHERE publication_id = %s ORDER BY id",
            (publication["publication_id"],),
        )
        receipt_count = inspect_rows(case, "SELECT count(*) FROM learning.activation_receipts")[0][0]

        invalid_path = tmp_path / "issue386-invalid.pgn"
        invalid_path.write_text(
            '[Event "Synthetic invalid PGN"]\n[Variant "Crazyhouse"]\n\n1. e4 e5 *\n'
        )
        invalid_manifest_path = tmp_path / "issue386-invalid.manifest.json"
        invalid = run_cli(
            content_cli,
            ["import", "manifest", invalid_path, "--workspace", "workspace-a",
             "--source-key", source_key, "--title", "Invalid synthetic source",
             "--color", "white", "--output", invalid_manifest_path],
            environment,
            tmp_path,
            success=False,
        )
        assert isinstance(invalid, subprocess.CompletedProcess)
        assert invalid.returncode == 2 and not invalid_manifest_path.exists()
        assert "invalid_pgn" in invalid.stderr

        changed_pgn = (
            '[Event "Synthetic stale revision"]\n\n'
            '1. d4 d5 2. c4 *\n'
        )
        changed_path, changed_manifest, _ = preview_manifest(
            content_cli, environment, tmp_path,
            source_key=source_key,
            title="Synthetic conflicting revision",
            color="white",
            pgn=changed_pgn,
        )
        stale_draft = run_cli(
            content_cli,
            ["import", "plan", changed_path, "--manifest", changed_manifest, "--expected-revision", 0],
            environment,
            tmp_path,
        )
        stale_approval = run_cli(
            content_cli,
            ["draft", "approve", stale_draft["id"], "--digest", stale_draft["digest"]],
            environment,
            tmp_path,
            success=False,
        )
        assert isinstance(stale_approval, subprocess.CompletedProcess)
        assert stale_approval.returncode == 2 and "stale_source_revision" in stale_approval.stderr

        later_status = client.get(status_path, headers=headers)
        assert later_status.status_code == 200
        assert {
            key: later_status.json()[key]
            for key in ("status", "publication_id", "publication_generation", "activation_revision", "local_date")
        } == prior_facts
        assert inspect_rows(
            case,
            "SELECT id,serving_active FROM learning.cards WHERE publication_id = %s ORDER BY id",
            (publication["publication_id"],),
        ) == active_cards
        assert inspect_rows(case, "SELECT count(*) FROM learning.activation_receipts")[0][0] == receipt_count
        current = run_cli(content_cli, ["source", "show", source_key], environment, tmp_path)
        assert current["requested_revision"] == 1
        evidence(
            "invalid-and-stale-revision.json",
            {
                "synthetic": True,
                "active_publication_id": publication["publication_id"],
                "active_generation": prior_status.json()["publication_generation"],
                "active_activation_revision": prior_status.json()["activation_revision"],
                "invalid_pgn_rejected_before_manifest": True,
                "stale_revision_approval_rejected_before_activation": True,
                "stale_draft_id": stale_draft["id"],
                "active_card_ids": [row[0] for row in active_cards],
                "activation_receipt_count_unchanged": True,
            },
        )
