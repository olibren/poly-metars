"""Rebuild supplied, verified airport days into SQL for a fresh Cloudflare D1 database.

No network calls or live mutations. Usage:
  python3 scripts/restore_cloudflare.py work/day1 work/day2 --output work/restore.sql
  npx wrangler d1 execute NEW_DATABASE --remote --file work/restore.sql --config wrangler.collector.jsonc
Create/apply the schema first and pause collection during restore. Does not restore
unpublished receipts, rejected reports, queue state or days not supplied.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger.archive import canonical
from ledger.audit import verify_export


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def restore_sql(directories):
    receipts, reports, days = {}, {}, set()
    for directory in directories:
        result = verify_export(directory)
        if not result.get("verified"):
            raise ValueError("Bundle verification failed")
        manifest = json.loads((directory / "audit.json").read_text())
        if manifest.get("schema") != "poly-metars-day-v1":
            raise ValueError("Supply an immutable airport-day bundle")
        for receipt in manifest["receipts"]:
            old = receipts.setdefault(receipt["id"], receipt)
            if old != receipt:
                raise ValueError("Conflicting receipt")
        for report in manifest["reports"]:
            # Different collectors may retain different first receipts for one identity.
            # Keep the earliest actual receipt, without changing any R2 revision.
            old = reports.get(report["id"])
            if not old or (report["fetched_at"], report["receipt_id"]) < (old["fetched_at"], old["receipt_id"]):
                reports[report["id"]] = report
        days.add((manifest["icao"], manifest["date"]))
    lines = ["-- Verified published days only; apply to an empty database with the schema installed."]
    for r in sorted(receipts.values(), key=lambda r:r["id"]):
        values = (r["id"], r["fetched_at"], canonical(r).decode())
        lines.append("INSERT OR IGNORE INTO receipts(id,fetched_at,payload) VALUES("+",".join(map(quote, values))+");")
    for r in sorted(reports.values(), key=lambda r:r["id"]):
        values = (r["id"], r["icao"], r["observed_at"], r["receipt_id"], canonical(r).decode())
        # Re-archive on next tick: the destination bucket may not have report objects yet.
        lines.append("INSERT OR IGNORE INTO reports(id,icao,observed_at,receipt_id,payload,archived) VALUES("+",".join(map(quote, values))+",0);")
    for icao, date in sorted(days):
        lines.append("INSERT OR IGNORE INTO dirty(icao,date) VALUES("+quote(icao)+","+quote(date)+");")
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sql = restore_sql(args.directories)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sql)
    print(f"Wrote verified restoration SQL to {args.output}; no live database changed.")


if __name__ == "__main__":
    main()
