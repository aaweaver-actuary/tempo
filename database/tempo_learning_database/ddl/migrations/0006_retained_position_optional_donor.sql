-- Preserve donor-free legacy introductions in non-serving staging.
ALTER TABLE retained_position_states
    ALTER COLUMN donor_card_id DROP NOT NULL,
    ALTER COLUMN donor_card_revision DROP NOT NULL,
    ALTER COLUMN donor_fingerprint DROP NOT NULL,
    ALTER COLUMN donor_selection_policy DROP NOT NULL,
    ADD CONSTRAINT retained_position_donor_complete CHECK (
        (donor_card_id IS NULL AND donor_card_revision IS NULL AND donor_fingerprint IS NULL AND donor_selection_policy IS NULL)
        OR
        (donor_card_id IS NOT NULL AND donor_card_revision IS NOT NULL AND donor_fingerprint IS NOT NULL AND donor_selection_policy IS NOT NULL)
    );
