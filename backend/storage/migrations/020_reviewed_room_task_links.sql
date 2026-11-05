-- A reviewed link refers to exact observations, not inferred room labels.
CREATE TABLE reviewed_room_task_links (
 task_observation_id TEXT PRIMARY KEY REFERENCES padsplit_task_observations(id),
 occupancy_observation_id BIGINT NOT NULL REFERENCES occupancy_observations(id),
 property_id TEXT NOT NULL REFERENCES properties(id),
 source_generation TEXT NOT NULL,
 reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX reviewed_room_task_links_occupancy ON reviewed_room_task_links(occupancy_observation_id);
