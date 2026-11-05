-- Links are explicit reviewed bindings to an exact source observation; no label inference.
CREATE TABLE reviewed_provider_discussion_links (
 task_observation_id TEXT PRIMARY KEY REFERENCES padsplit_task_observations(id) ON DELETE CASCADE,
 internal_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 property_id TEXT NOT NULL REFERENCES properties(id),
 source_generation TEXT NOT NULL,
 expected_task_source_path TEXT,
 reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX reviewed_provider_discussion_links_internal ON reviewed_provider_discussion_links(internal_task_id);
