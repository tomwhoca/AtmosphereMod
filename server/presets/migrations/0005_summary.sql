-- The versions list, kept (2026-11-23): GET /v1/versions read every public preset's every value (json_each), about
-- 1,200 rows a call, 97% of the database's reads, near the free plan's 5 million a day. The list is now one row here,
-- made again in the same batch as each change that can alter it (share, update, unshare, the admin's restore, hide,
-- remove: src/index.ts VERSIONS_REFRESH); a call reads that row.

CREATE TABLE summary (
  name TEXT PRIMARY KEY,
  body TEXT NOT NULL,                       -- JSON
  updated INTEGER NOT NULL                  -- unix ms
);

INSERT INTO summary (name, body, updated)
SELECT 'versions', json_group_array(json_object('version', v.version, 'count', v.count, 'keys', json(v.keys))),
       CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER)
FROM (SELECT p.version AS version, COUNT(DISTINCT p.id) AS count, json_group_array(DISTINCT j.key) AS keys
      FROM presets p, json_each(p.vals) j WHERE p.status = 'public' AND p.version != '' GROUP BY p.version) v
WHERE true
ON CONFLICT (name) DO UPDATE SET body = excluded.body, updated = excluded.updated;
