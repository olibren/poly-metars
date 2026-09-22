"""Sync Python Worker dependencies with the collector's explicit configuration.

workers-py 1.17 reads only a root wrangler.jsonc/toml while syncing, even when the
forwarded Wrangler command uses --config. Expose the reviewed config only for the
sync, then remove it so vinext correctly builds the viewer as static assets.
"""
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
default = root / 'wrangler.jsonc'
if default.exists():
    raise SystemExit('Unexpected root wrangler.jsonc; review it before syncing. Use wrangler.collector.jsonc.')
try:
    shutil.copyfile(root / 'wrangler.collector.jsonc', default)
    subprocess.run(['uv', 'run', 'pywrangler', 'sync', *sys.argv[1:]], cwd=root, check=True)
finally:
    default.unlink(missing_ok=True)
