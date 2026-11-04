-- Preserve every existing queue, attempt, and projection while tracking freshness.
ALTER TABLE queue_projections ADD COLUMN requested_refresh_epoch BIGINT NOT NULL DEFAULT 0;
ALTER TABLE queue_projections ADD COLUMN published_refresh_epoch BIGINT NOT NULL DEFAULT 0;
ALTER TABLE queue_projections ADD CONSTRAINT queue_refresh_epoch_order
  CHECK (requested_refresh_epoch >= 0 AND published_refresh_epoch >= 0
         AND published_refresh_epoch <= requested_refresh_epoch);
