"""Independent replay checks using the published bundle, with no network access."""

from pathlib import Path
import json
from .archive import canonical, digest
from .metar import parse_time
from .policy import daily
from .evidence import verify_evidence


def verify_export(directory):
    root = Path(directory)
    if (root / "audit.json").exists():
        return verify_live_day(root)
    if (root / "index.json").exists():
        index = json.loads((root / "index.json").read_text())
        if (
            not (root / "manifest.json").exists()
            or (root / "snapshots" / index["snapshot_id"]).exists()
        ):
            root = root / "snapshots" / index["snapshot_id"]
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe manifest path")
        if digest(path.read_bytes()) != expected:
            raise ValueError("File hash mismatch: " + name)
    policy = json.loads((root / "policy.json").read_text())
    airports = json.loads((root / "airports.json").read_text())
    if digest(canonical(policy)) != manifest["policy_sha256"]:
        raise ValueError("Policy mismatch")
    if digest(canonical(airports)) != manifest["registry_sha256"]:
        raise ValueError("Registry mismatch")
    records = [
        json.loads(line)
        for line in (root / "reports.jsonl").read_text().splitlines()
        if line
    ]
    allowed = set(manifest["report_ids"])
    reports = [r for r in records if r["id"] in allowed]
    if {r["id"] for r in reports} != allowed:
        raise ValueError("Missing report inputs")
    receipts = [
        json.loads(line)
        for line in (root / "receipts.jsonl").read_text().splitlines()
        if line
    ]
    verify_evidence(reports, receipts, root / "evidence")
    checked = 0
    for date in manifest["dates"]:
        for airport in airports:
            actual = json.loads(
                (root / f"days/{date}/{airport['icao']}.json").read_text()
            )
            expected = daily(
                airport, date, reports, policy, parse_time(manifest["generated_at"])
            )
            expected.update(
                {
                    k: manifest[k]
                    for k in (
                        "generated_at",
                        "policy_sha256",
                        "registry_sha256",
                        "snapshot_id",
                    )
                }
            )
            if actual != expected:
                raise ValueError(f"Replay mismatch: {date}/{airport['icao']}")
            checked += 1
    return {
        "verified": True,
        "files": len(manifest["files"]),
        "replayed_airport_days": checked,
    }



def verify_live_day(root):
    manifest = json.loads((root / 'audit.json').read_text())
    if manifest['schema'] != 'poly-metars-day-v1':
        raise ValueError('Unknown live audit schema')
    identity = {k: manifest[k] for k in ('date', 'icao', 'report_ids', 'policy_sha256', 'registry_sha256', 'engine_sha256')}
    if digest(canonical(identity)) != manifest['revision']:
        raise ValueError('Revision identity mismatch')
    if digest(canonical(manifest['policy'])) != manifest['policy_sha256']:
        raise ValueError('Policy mismatch')
    if digest(canonical(manifest['registry'])) != manifest['registry_sha256'] or manifest['airport'] not in manifest['registry']:
        raise ValueError('Registry mismatch')
    if manifest['airport']['icao'] != manifest['icao']:
        raise ValueError('Airport mismatch')
    if sorted(r['id'] for r in manifest['reports']) != manifest['report_ids']:
        raise ValueError('Report set mismatch')
    verify_evidence(manifest['reports'], manifest['receipts'], root / 'evidence')
    expected = daily(manifest['airport'], manifest['date'], manifest['reports'], manifest['policy'], parse_time(manifest['generated_at']))
    expected.update({'generated_at': manifest['generated_at'], 'policy_sha256': manifest['policy_sha256'],
                     'registry_sha256': manifest['registry_sha256'], 'snapshot_id': manifest['revision']})
    if expected != json.loads((root / 'day.json').read_text()):
        raise ValueError('Live day replay mismatch')
    return {'verified': True, 'reports': len(manifest['reports']), 'replayed_airport_days': 1}

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default="public/data")
    print(json.dumps(verify_export(parser.parse_args().directory)))
