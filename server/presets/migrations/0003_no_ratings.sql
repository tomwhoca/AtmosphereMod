-- No ratings (2026-11-21: the user, on a rethink; Most added is enough) and no reports (gone from the app the same
-- day). Their tables and columns go; none held anything real.

DROP TABLE ratings;
ALTER TABLE presets DROP COLUMN rating_sum;
ALTER TABLE presets DROP COLUMN rating_count;
DROP TABLE reports;
ALTER TABLE presets DROP COLUMN reports;
