-- The advisory prefix-transition workflow is retired. Old application/fence
-- records remain as history, but they must not block retained authored-line,
-- graph, card, or permanent-deletion writes after its worker is removed.
--
-- Drop only the scope checks introduced by 038/039. Keep the shared queue
-- reservation triggers, permanent_deletion_reservation, and
-- prevent_deleted_card_recreation: their advisory lock still serializes
-- retained permanent-deletion and content recreation boundaries.
DROP TRIGGER prefix_transition_card_write ON cards;
DROP TRIGGER prefix_transition_line_write ON repertoire_lines;
DROP TRIGGER prefix_transition_depth_write ON repertoire_line_training_depths;
DROP TRIGGER prefix_transition_membership_write ON repertoire_cards;
DROP TRIGGER prefix_transition_repertoire_write ON repertoires;
DROP TRIGGER prefix_transition_graph_request ON background_tasks;
DROP TRIGGER prefix_transition_publication_write ON opening_graph_publications;
DROP TRIGGER prefix_transition_split_write ON prefix_splits;
DROP TRIGGER prefix_transition_steps_write ON opening_graph_steps;
DROP TRIGGER permanent_deletion_scope ON deleted_cards;

DROP FUNCTION guard_prefix_transition_write() RESTRICT;
DROP FUNCTION guard_permanent_deletion_scope() RESTRICT;
DROP FUNCTION guard_prefix_transition_scope(TEXT, TEXT) RESTRICT;

INSERT INTO tempo_schema_migrations(version) VALUES(63);
