"""Consistent SQLite backup and append-only off-host evidence copy. No deletion."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', default='/var/lib/poly-metars')
parser.add_argument('--bucket', required=True)
args = parser.parse_args()
root = Path(args.root)
stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')
backup = root / 'backup.sqlite3'
with sqlite3.connect(root / 'archive/ledger.sqlite3') as source, sqlite3.connect(backup) as target:
    source.backup(target)
for directory, prefix in [('archive/objects', 'evidence'), ('public', 'public')]:
    subprocess.run(['aws','s3','sync',str(root / directory),f's3://{args.bucket}/{prefix}/','--only-show-errors'],check=True)
subprocess.run(['aws','s3','cp',str(backup),f's3://{args.bucket}/database/{stamp}.sqlite3','--only-show-errors'],check=True)
(root / 'backup-status.json').write_text('{"last_success_at":"' + datetime.now(timezone.utc).isoformat().replace('+00:00','Z') + '"}\n')
