-- Durable acceptance is distinct from source receipt and HTTP retrieval time.
-- Existing archived evidence is known present at this migration, not retroactively.
ALTER TABLE reports ADD COLUMN accepted_at INTEGER;
UPDATE reports SET accepted_at=unixepoch('now') WHERE archived=1;
INSERT OR IGNORE INTO state(name,value) VALUES('midnight_lock_started_at',CAST(unixepoch('now') AS TEXT));
-- Keep the bounded mixed-version repair cheap after rollout completes.
CREATE INDEX report_acceptance_pending ON reports(id) WHERE archived=1 AND accepted_at IS NULL;
