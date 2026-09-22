"""Import a verified legacy snapshot without altering its source evidence or times."""
import argparse
import json
from pathlib import Path
import shutil
from ledger.live import Store
from ledger.audit import verify_export

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('snapshot')
parser.add_argument('root')
args = parser.parse_args()
source = Path(args.snapshot)
print(verify_export(source), flush=True)
store = Store(Path(args.root) / 'archive')
with store.lock():
    for path in (source / 'evidence').glob('*.txt'):
        target = store.root / 'objects' / path.name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    receipts = {}
    for line in (source / 'receipts.jsonl').read_text().splitlines():
        row = json.loads(line)
        store.append('receipts', row)
        receipts[row['id']] = row
    count = 0
    for line in (source / 'reports.jsonl').read_text().splitlines():
        row = json.loads(line)
        parsed = {k:v for k,v in row.items() if k not in ('id','source','receipt_id','url','fetched_at','body_sha256')}
        count += store.report(parsed, receipts[row['receipt_id']])
    print({'imported_reports':count,'verified':store.verify()})
