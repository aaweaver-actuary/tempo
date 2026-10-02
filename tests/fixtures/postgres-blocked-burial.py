"""Prepare a real foreground burial receipt using the production retry lifecycle."""
import sys
from app import postgres_store, queue_commands  # Register the production handler.
from app.command_gateway import record_operation_attempt, record_operation_retry, load_blocked_operation

assert postgres_store.configured()
entry_id = int(sys.argv[1])
operation_id = sys.argv[2]
payload = {"entry_id": entry_id}
execute, saved_payload, attempt_token, _ = record_operation_attempt(
    operation_id, "queue.bury", payload, background=False)
assert execute and saved_payload == payload
exhausted, _ = record_operation_retry(operation_id, attempt_token,
    RuntimeError("Synthetic recoverable burial interruption"), retryable=False, background=False)
assert exhausted
blocked = load_blocked_operation(operation_id)
assert blocked["payload"] == payload and blocked["background"] is False
assert blocked["retry_cycle"] == 0
