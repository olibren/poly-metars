-- D1 is a rebuildable working index. Original responses and receipts live in R2.
CREATE TABLE receipts (id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX receipt_age ON receipts(fetched_at);
CREATE TABLE reports (id TEXT PRIMARY KEY, icao TEXT NOT NULL, observed_at TEXT NOT NULL,
  receipt_id TEXT NOT NULL, payload TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE INDEX report_day ON reports(icao, observed_at);
CREATE TABLE rejected (id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, interval_seconds INTEGER NOT NULL, expires_at INTEGER NOT NULL,
  next_due INTEGER NOT NULL DEFAULT 0, queued_until INTEGER NOT NULL DEFAULT 0,
  lease_until INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
  last_attempt INTEGER, last_success INTEGER, last_error TEXT, reports INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX task_due ON tasks(mode, next_due, queued_until);
CREATE INDEX task_expiry ON tasks(expires_at);
CREATE TABLE state (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE dirty (icao TEXT NOT NULL, date TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(icao,date));
