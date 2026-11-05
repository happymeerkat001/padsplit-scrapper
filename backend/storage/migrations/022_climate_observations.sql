-- Private source readings are scoped only by an operator-reviewed property mapping.
CREATE TABLE climate_observations (
  id TEXT PRIMARY KEY,
  device_key TEXT NOT NULL,
  property_id TEXT NOT NULL REFERENCES properties(id),
  observed_at TIMESTAMPTZ NOT NULL,
  source_fingerprint TEXT NOT NULL CHECK (source_fingerprint ~ '^[a-f0-9]{64}$'),
  data JSONB NOT NULL CHECK (jsonb_typeof(data)='object'),
  UNIQUE(device_key,observed_at)
);
CREATE INDEX climate_observations_latest ON climate_observations(device_key,observed_at DESC);
CREATE INDEX climate_observations_property ON climate_observations(property_id,observed_at DESC);
