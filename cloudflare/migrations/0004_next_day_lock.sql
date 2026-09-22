-- Prospective v4 activation. Previously closed days retain their v3 contract.
INSERT OR IGNORE INTO state(name,value) VALUES('next_day_lock_started_at',CAST(unixepoch('now') AS TEXT));
