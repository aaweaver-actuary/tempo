import time


def wait_for_integrity(client, repertoire_id: str):
    """Wait for the durable integrity generation used by workflow tests."""

    payload = None
    for _ in range(200):
        payload = client.get(f"/api/repertoires/{repertoire_id}/integrity").json()
        if payload.get("scan_status") in {"idle", "failed"}:
            for _ in range(200):
                task_status = client.get("/api/system/tasks").json()
                active_projection_tasks = [
                    task
                    for task in task_status["tasks"]
                    if task["kind"] in {"daily_queue", "opening_graph_rebuild"}
                    and task["state"] in {"queued", "leased", "retrying"}
                ]
                if not active_projection_tasks:
                    return payload
                time.sleep(0.01)
            return payload
        time.sleep(0.01)
    return payload


def wait_for_daily_queue(client, expected_count: int | None = None):
    payload = None
    for _ in range(200):
        tasks = client.get("/api/system/tasks").json()["tasks"]
        payload = client.get("/api/queue/today").json()
        active = any(
            task["kind"] in {"daily_queue", "opening_graph_rebuild"}
            and task["state"] in {"queued", "leased", "retrying"}
            for task in tasks
        )
        if not active and (
            expected_count is None or payload["count"] == expected_count
        ):
            return payload
        time.sleep(0.01)
    return payload
