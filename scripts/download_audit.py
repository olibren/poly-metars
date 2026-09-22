"""Download one immutable day and its evidence; then run the existing offline verifier.

Usage: python3 scripts/download_audit.py https://SITE/data/revisions/HASH/audit.json work/audit
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger.audit import verify_export


def download(url, origin):
    parsed = urlparse(url)
    if (parsed.scheme, parsed.netloc) != origin:
        raise ValueError("Evidence must stay on the requested origin")
    with urlopen(Request(url, headers={"User-Agent": "PolyMETARs-Audit/1"}), timeout=30) as response:
        final = urlparse(response.url)
        if (final.scheme, final.netloc) != origin:
            raise ValueError("Unexpected redirect")
        body = response.read(32_000_001)
        if len(body) > 32_000_000:
            raise ValueError("Audit file exceeds 32 MB limit")
        return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest_url")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    parsed = urlparse(args.manifest_url)
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        parser.error("Use HTTPS for public evidence")
    match = re.fullmatch(r"(.*/revisions/)([a-f0-9]{64})/audit.json", parsed.path)
    if not match or parsed.query or parsed.fragment:
        parser.error("Use the immutable airport-day audit manifest URL")
    origin = (parsed.scheme, parsed.netloc)
    root = args.directory
    root.mkdir(parents=True, exist_ok=True)
    manifest_bytes = download(args.manifest_url, origin)
    manifest = json.loads(manifest_bytes)
    if manifest["revision"] != match[2]:
        raise ValueError("Manifest does not match requested revision")
    (root / "audit.json").write_bytes(manifest_bytes)
    (root / "day.json").write_bytes(download(urljoin(args.manifest_url, "day.json"), origin))
    evidence_url = f"{parsed.scheme}://{parsed.netloc}" + match[1].removesuffix("revisions/") + "evidence/"
    (root / "evidence").mkdir(exist_ok=True)
    for body_hash in manifest["evidence"]:
        if not re.fullmatch("[a-f0-9]{64}", body_hash):
            raise ValueError("Invalid evidence hash")
        path = root / "evidence" / f"{body_hash}.txt"
        body = path.read_bytes() if path.exists() else download(evidence_url+path.name, origin)
        if hashlib.sha256(body).hexdigest() != body_hash:
            raise ValueError("Evidence hash mismatch: "+body_hash)
        path.write_bytes(body)
    print(json.dumps(verify_export(root)))


if __name__ == "__main__":
    main()
