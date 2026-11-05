-- Retain the exact source fields for each observation. No scope is inferred.
ALTER TABLE occupancy_observations ADD COLUMN source_payload JSONB;
ALTER TABLE occupancy_observations ADD COLUMN source_generation TEXT;
