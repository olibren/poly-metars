"""Append-only receipts and records; exact response bytes addressed by SHA-256."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import tempfile
from .metar import iso


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n")


class Archive:
    def __init__(self, root):
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

    def read(self, name):
        path = self.root / f"{name}.jsonl"
        if not path.exists():
            return []
        # Fail visibly on corruption instead of silently skipping damaged evidence.
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def append(self, name, value):
        with (self.root / f"{name}.jsonl").open("ab") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

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
        from .evidence import verify_evidence

        receipts = {r["id"]: r for r in self.read("receipts")}
        reports = self.read("reports")
        verify_evidence(reports, list(receipts.values()), self.root / "objects")
        return {"receipts": len(receipts), "reports": len(reports), "verified": True}
