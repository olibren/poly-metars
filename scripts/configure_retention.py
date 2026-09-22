"""Apply the reviewed retention policy to this project's dedicated R2 bucket.

Deploy the collector's shared-evidence refresh first. This replaces the bucket's
lifecycle configuration, including its seven-day incomplete-upload cleanup rule.
"""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def lifecycle(retention):
    return {"rules": [
        {"id": "poly-metars-retention", "enabled": True, "conditions": {"prefix": ""},
         "deleteObjectsTransition": {"condition": {"type": "Age", "maxAge": retention["archive_expire_days"]*86400}}},
        {"id": "Default Multipart Abort Rule", "enabled": True, "conditions": {"prefix": ""},
         "abortMultipartUploadsTransition": {"condition": {"type": "Age", "maxAge": 7*86400}}},
    ]}


if __name__ == "__main__":
    retention = json.loads((ROOT/"config/retention.json").read_text())
    deployment = json.loads((ROOT/"deploy/cloudflare.json").read_text())
    with tempfile.TemporaryDirectory(prefix="poly-metars-retention-") as directory:
        path = Path(directory)/"lifecycle.json"
        path.write_text(json.dumps(lifecycle(retention), indent=2)+"\n")
        subprocess.run(["npx", "wrangler", "r2", "bucket", "lifecycle", "set", deployment["bucket"],
                        "--file", str(path), "--config", "wrangler.collector.jsonc", "--force"],
                       cwd=ROOT, check=True)
