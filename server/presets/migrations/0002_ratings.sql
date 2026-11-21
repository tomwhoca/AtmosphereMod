-- Ratings (2026-11-21): 1 to 5 stars, one per client and preset (a client may change theirs). The presets keep the
-- sum and the count for listing and sorting. (Reports went from the app the same day; their table stays unused.)

ALTER TABLE presets ADD COLUMN rating_sum INTEGER NOT NULL DEFAULT 0;
ALTER TABLE presets ADD COLUMN rating_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE ratings (
  preset_id TEXT NOT NULL,
  client TEXT NOT NULL,
  stars INTEGER NOT NULL,
  created INTEGER NOT NULL,
  PRIMARY KEY (preset_id, client)
);
