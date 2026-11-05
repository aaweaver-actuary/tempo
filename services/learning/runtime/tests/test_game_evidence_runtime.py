"""A saved Learning obligation survives restart and a lost browser acknowledgement."""
import json
from pathlib import Path
import subprocess
import uuid

from test_evidence_revisions import send
from test_remediation import activate_without_queue_work, evidence
from test_restart import server
from test_runtime import bundle


def test_native_browser_settles_accepted_remediation_and_introduces_ordinary_work_with_producer_stopped(
    case, tmp_path
):
    payload = bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [[move]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
            for move in ["e2e4", "d2d4"]
        ]
    )
    activate_without_queue_work(case, payload)
    with server(case, tmp_path) as (client, process):
        response = send(case, evidence(payload["publication"]["definitions"][0]), client)
        assert response.status_code == 200 and response.json()["status"] == "completed"
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 0
    script = Path(__file__).resolve().parents[1] / "browser/prove.mjs"
    with server(case, tmp_path) as (client, process):
        proof = subprocess.run(
            ["node", str(script)],
            input=json.dumps(
                {
                    "url": str(client.base_url).rstrip("/"),
                    "token": case.configuration.token,
                    "loss": True,
                    "remediation": True,
                }
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proof.returncode == 0, (proof.stdout + proof.stderr)[-6000:]
        result = json.loads(proof.stdout)
        assert result["complete"] and result["remediation"] and result["remaining"] >= 1
        from test_runtime import review_payload

        reinforcement_reviews = 0
        for _ in range(3):
            ordinary = client.get("/api/queue/window").json()["cards"][0]
            if ordinary["id"] == payload["publication"]["definitions"][1]["card_id"]:
                break
            # Keep first-correct reinforcement under the existing scheduler.
            assert ordinary["id"] == payload["publication"]["definitions"][0]["card_id"]
            assert ordinary["attempt_state"] == "reinforcement"
            response = client.post(
                f"/api/cards/{ordinary['id']}/review",
                json=review_payload(ordinary),
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            assert response.status_code == 200, response.text
            reinforcement_reviews += 1
        assert ordinary["id"] == payload["publication"]["definitions"][1]["card_id"]
        assert (
            ordinary["admission_kind"] == "new"
            and not ordinary["gameplay_priority_reason"]
        )
        original = review_payload(ordinary)
        operation = uuid.uuid4().hex
        saved = client.post(
            f"/api/cards/{ordinary['id']}/review",
            json=original,
            headers={"Idempotency-Key": operation},
        )
        assert saved.status_code == 200, saved.text
        assert (
            client.post(
                f"/api/cards/{ordinary['id']}/review",
                json=original,
                headers={"Idempotency-Key": operation},
            ).json()
            == saved.json()
        )
    assert (
        case.query("SELECT COUNT(*) FROM learning.reviews")[0][0]
        == 2 + reinforcement_reviews
    )
    assert (
        case.query(
            "SELECT COUNT(*) FROM learning.remediation_review_receipts WHERE settled"
        )[0][0]
        == 1
    )
