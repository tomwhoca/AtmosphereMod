-- Community Presets: the shared presets, the reports against them, and the daily submission counts.
-- No IP address is stored anywhere: clients are keyed hashes (src/index.ts clientKey).

CREATE TABLE presets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  author TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  version TEXT NOT NULL DEFAULT '',
  vals TEXT NOT NULL,                       -- JSON {"T0": 1, "C16": 0.5, ...}: only the look (T and C)
  vals_hash TEXT NOT NULL,                  -- the values' fingerprint: the same preset isn't listed twice
  status TEXT NOT NULL DEFAULT 'public',    -- public | hidden (reported, until the admin looks) | removed
  created INTEGER NOT NULL,                 -- unix ms
  downloads INTEGER NOT NULL DEFAULT 0,
  reports INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX presets_status_created ON presets (status, created DESC);
CREATE INDEX presets_vals_hash ON presets (vals_hash);

-- one report per client and preset
CREATE TABLE reports (
  preset_id TEXT NOT NULL,
  client TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL,
  PRIMARY KEY (preset_id, client)
);

-- submissions per client and day (the rate limit); the client key changes daily
CREATE TABLE submissions (
  client TEXT NOT NULL,
  day TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (client, day)
);
