ALTER TABLE content.bridge_operations DROP CONSTRAINT bridge_operations_state_check;
ALTER TABLE content.bridge_operations ADD CONSTRAINT bridge_operations_state_check CHECK(state IN ('approved','applying','completed','cancelled','failed'));
