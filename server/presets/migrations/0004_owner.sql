-- Update or unshare (2026-11-21): a shared preset's owner is the PC that shared it, by an edit key the server
-- gave it then. Only the key's keyed hash is kept (owner_hash; empty for presets shared before: the admin manages
-- those). An update sets updated; an unshare sets the status 'unshared'.

ALTER TABLE presets ADD COLUMN owner_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE presets ADD COLUMN updated INTEGER NOT NULL DEFAULT 0;
