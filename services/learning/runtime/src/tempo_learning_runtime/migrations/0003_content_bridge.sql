CREATE TABLE content_card_aliases(content_card_id text PRIMARY KEY, learning_card_id text UNIQUE NOT NULL, publication_id text NOT NULL, binding_digest text NOT NULL);
