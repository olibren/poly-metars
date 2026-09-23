"""Publish review documents and an explicit, secret-free source download."""

from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
(root / "public").mkdir(exist_ok=True)
for name in ("README.md", "POLICY.md", "LICENSE"):
    shutil.copyfile(root / name, root / "public" / name)
shutil.copyfile(root / "docs/AUDIT.md", root / "public/AUDIT.md")

# An allowlist keeps archives, local credentials and dependency trees out.
files = [
    root / name
    for name in (
        "README.md",
        "POLICY.md",
        "LICENSE",
        "CHANGELOG.md",
        "Makefile",
        ".gitignore",
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "next.config.ts",
        "vite.config.ts",
        ".oxlintrc.json",
        ".oxfmtrc.json",
        "public/favicon.svg",
        ".github/workflows/check.yml",
        "pyproject.toml",
        "uv.lock",
        "pylock.toml",
        "wrangler.collector.jsonc",
        "wrangler.site.jsonc",
        "cloudflare/tsconfig.json",
        "cloudflare/deployment.json",
    )
]
for directory, pattern in (
    ("ledger", "*.py"),
    ("tests", "*.py"),
    ("tests", "*.mjs"),
    ("scripts", "*.py"),
    ("docs", "*.md"),
    ("config", "*.json"),
    ("src", "*.tsx"),
    ("src", "*.css"),
    ("src", "*.ts"),
):
    files.extend((root / directory).rglob(pattern))
files.extend(path for path in (root / "cloudflare/src").glob("*.py")
             if path.name != "settings.py")
files.extend((root / "cloudflare").glob("*.ts"))
files.extend((root / "cloudflare/migrations").glob("*.sql"))
# Local agent instructions are never part of the public source distribution.
files = [path for path in files if path.name not in {"AGENTS.md", "CLAUDE.md"}]
with zipfile.ZipFile(root / "public/source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, "poly-metars/" + path.relative_to(root).as_posix())
print(f"Published review documents and {len(set(files))} source files.")
