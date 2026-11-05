-- Provider tickets are read-only observed source records, separate from editable manual tasks.
CREATE TABLE padsplit_task_observations (
 id TEXT PRIMARY KEY,
 task_key TEXT NOT NULL,
 source_id JSONB NOT NULL,
 property_id TEXT REFERENCES properties(id),
 bucket TEXT NOT NULL,
 data JSONB NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL,
 source_generation TEXT NOT NULL,
 UNIQUE(task_key, observed_at)
);
CREATE INDEX padsplit_task_observations_latest ON padsplit_task_observations(task_key, observed_at DESC);
CREATE INDEX padsplit_task_observations_property ON padsplit_task_observations(property_id, observed_at DESC);
