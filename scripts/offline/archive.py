"""Local append-only evidence capture for offline fixtures and registry review."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
import fcntl
import json
import os
import threading
from ledger.metar import iso
from ledger.archive import canonical, digest, atomic_write


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.mutex:
            return method(self, *args, **kwargs)
    return wrapped


class Archive:
    def __init__(self, root):
        self.mutex = threading.RLock()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ids = {r["id"] for r in self.read("reports")}

    @contextmanager
    def lock(self):
        with (self.root / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @synchronized
    def read(self, name):
        path = self.root / f"{name}.jsonl"
        if not path.exists():
            return []
        # Fail visibly on corruption instead of silently skipping damaged evidence.
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    @synchronized
    def append(self, name, value):
        with (self.root / f"{name}.jsonl").open("ab") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    @synchronized
    def response(self, source, url, body, status, headers=None, error=None):
        body_hash = digest(body)
        path = self.root / "objects" / f"{body_hash}.txt"
        if not path.exists():
            atomic_write(path, body)
        receipt = {
            "source": source,
            "url": url,
            "fetched_at": iso(datetime.now(timezone.utc)),
            "status": status,
            "body_sha256": body_hash,
            "bytes": len(body),
            "headers": headers or {},
            "error": error,
        }
        receipt["id"] = digest(canonical(receipt))
        self.append("receipts", receipt)
        return receipt

    @synchronized
    def report(self, parsed, receipt):
        if "parse_error" in parsed:
            self.append("rejected", {**parsed, "receipt_id": receipt["id"]})
            return False
        identity = {**parsed, "source": receipt["source"]}
        record_id = digest(canonical(identity))
        if record_id in self.ids:
            return False
        record = {
            **identity,
            "id": record_id,
            "receipt_id": receipt["id"],
            "url": receipt["url"],
            "fetched_at": receipt["fetched_at"],
            "body_sha256": receipt["body_sha256"],
        }
        self.append("reports", record)
        self.ids.add(record_id)
        return True

    def verify(self):
        from ledger.evidence import verify_evidence

        receipts = {r["id"]: r for r in self.read("receipts")}
        reports = self.read("reports")
        verify_evidence(reports, list(receipts.values()), self.root / "objects")
        return {"receipts": len(receipts), "reports": len(reports), "verified": True}
