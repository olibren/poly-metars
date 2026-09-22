"""Publish static, portable JSON/CSV plus a content-addressed reproducibility bundle."""

from datetime import datetime
from pathlib import Path
import csv
import io
import json
import os
import shutil
import zipfile
from .archive import canonical, digest, write_json, atomic_write
from .metar import UTC, iso
from .policy import daily


def export(archive, config, output, dates=None, now=None):
    output = Path(output)
    config = Path(config)
    now = now or datetime.now(UTC)
    airports = json.loads((config / "airports.json").read_text())
    policy = json.loads((config / "policy.json").read_text())
    sources = json.loads((config / "sources.json").read_text())
    # The policy defines the actual column order, not the sources file's incidental order.
    source_map = {s["id"]: s for s in sources}
    sources = [source_map[s] for s in policy["source_order"]]
    reports = archive.read("reports")
    runs = archive.read("runs")
    if dates is None:
        dates = sorted({d for run in runs for d in run.get("dates", [])}, reverse=True)
    policy_hash = digest(canonical(policy))
    registry_hash = digest(canonical(airports))
    engine_hashes = {
        p.name: digest(p.read_bytes())
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    archive_bytes = {
        name: (archive.root / f"{name}.jsonl").read_bytes()
        if (archive.root / f"{name}.jsonl").exists()
        else b""
        for name in ("reports", "receipts", "rejected", "runs")
    }
    snapshot = {
        "generated_at": iso(now),
        "policy_sha256": policy_hash,
        "registry_sha256": registry_hash,
        "engine_sha256": engine_hashes,
        "archive_sha256": {name: digest(data) for name, data in archive_bytes.items()},
        "sources_sha256": digest(canonical(sources)),
        "report_ids": sorted(r["id"] for r in reports),
        "dates": sorted(dates),
    }
    snapshot_id = digest(canonical(snapshot))
    public_root = output
    output = public_root / "snapshots" / snapshot_id
    output.mkdir(parents=True, exist_ok=True)
    files = {}

    def emit(relative, value):
        data = json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n"
        atomic_write(output / relative, data)
        files[relative] = digest(data)

    for date in dates:
        for airport in airports:
            result = daily(airport, date, reports, policy, now)
            result.update(
                {
                    "generated_at": iso(now),
                    "policy_sha256": policy_hash,
                    "registry_sha256": registry_hash,
                    "snapshot_id": snapshot_id,
                }
            )
            emit(f"days/{date}/{airport['icao']}.json", result)
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "observation_utc",
                    "local_time",
                    "selected_c",
                    "selected_source",
                    "status",
                    *policy["source_order"],
                ]
            )
            for row in result["rows"]:
                selected = row["selected"] or {}
                writer.writerow(
                    [
                        row["observed_at"],
                        row["local_time"],
                        selected.get("temperature_c", ""),
                        selected.get("source", ""),
                        row["status"],
                        *[
                            (row["sources"][s]["report"] or {}).get("temperature_c", "")
                            for s in policy["source_order"]
                        ],
                    ]
                )
            csv_data = buffer.getvalue().encode()
            name = f"days/{date}/{airport['icao']}.csv"
            atomic_write(output / name, csv_data)
            files[name] = digest(csv_data)
    latest = {}
    for run in runs:
        for source in run.get("results", []):
            latest[source["source"]] = {
                **source,
                "finished_at": run.get("finished_at"),
                "airports": run.get("airports", []),
                "dates": run.get("dates", []),
            }
    index = {
        "generated_at": iso(now),
        "snapshot_id": snapshot_id,
        "base_path": "/data/snapshots/" + snapshot_id,
        "airports": airports,
        "dates": sorted(dates, reverse=True),
        "sources": sources,
        "policy": policy,
        "collection": list(latest.values()),
        "rejected_count": len(archive.read("rejected")),
        "catalog_note": "Airport registry is linked to published temperature markets. Schedule expectations are reviewable estimates, not proof that every routine report was issued. Non-airport markets are excluded.",
    }
    emit("index.json", index)
    emit("policy.json", policy)
    emit("airports.json", airports)
    emit("sources.json", sources)
    for name, data in archive_bytes.items():
        atomic_write(output / f"{name}.jsonl", data)
        files[f"{name}.jsonl"] = digest(data)
    referenced = {r["body_sha256"] for r in archive.read("receipts")}
    for body_hash in sorted(referenced):
        origin = archive.root / "objects" / f"{body_hash}.txt"
        destination = output / "evidence" / origin.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            # Content-addressed bodies are write-once; share their disk blocks across snapshots.
            try:
                os.link(origin, destination)
            except OSError:
                shutil.copyfile(origin, destination)
        files["evidence/" + origin.name] = body_hash
    manifest = {
        **snapshot,
        "snapshot_id": snapshot_id,
        "files": files,
        "integrity_note": "Hashes detect changes relative to this manifest. They are not government signatures or independent proof of receipt time.",
    }
    write_json(output / "manifest.json", manifest)
    # The portable bundle contains the manifest and everything it references.
    # It is a transport container, so it is not recursively listed in that manifest.
    temporary_bundle = output / ".bundle.zip"
    with zipfile.ZipFile(temporary_bundle, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in [*sorted(files), "manifest.json"]:
            bundle.write(output / name, name)
    with temporary_bundle.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary_bundle, output / "bundle.zip")
    directory = os.open(output, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    # Preserve each published manifest and its config. Exact reports remain append-only.
    history = archive.root / "snapshots" / snapshot_id
    if not history.exists():
        write_json(history / "manifest.json", manifest)
        write_json(history / "policy.json", policy)
        write_json(history / "airports.json", airports)
    # Only advertise a release after every data and evidence file is durable.
    # The client follows immutable URLs, so concurrent publication cannot mix releases.
    write_json(public_root / "index.json", index)
    return {
        "snapshot_id": snapshot_id,
        "airports": len(airports),
        "dates": dates,
        "reports": len(reports),
    }
