"""Focused real Chromium + existing client functions + installed Learning HTTP."""

from pathlib import Path
import json, subprocess
import pytest
from test_restart import server
from test_runtime import activate


@pytest.mark.parametrize(
    "loss", [False, True], ids=["progression", "lost-response-reload"]
)
def test_native_existing_review_client_progression_and_recovery(case, tmp_path, loss):
    activate(case)
    script = Path(__file__).resolve().parents[1] / "browser/prove.mjs"
    with server(case, tmp_path) as (client, process):
        result = subprocess.run(
            ["node", str(script)],
            input=json.dumps(
                {
                    "url": str(client.base_url).rstrip("/"),
                    "token": case.configuration.token,
                    "loss": loss,
                }
            ),
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
        proof = json.loads(result.stdout)
        assert proof["complete"] is True
    assert case.query("SELECT COUNT(*) FROM learning.reviews")[0][0] == 1
    assert (
        case.query("SELECT COUNT(*) FROM learning.review_attempt_receipts")[0][0] == 1
    )
