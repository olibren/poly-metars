"""Publish review documents and an explicit, secret-free source download."""

from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
for name in ("README.md", "POLICY.md", "LICENSE"):
    shutil.copyfile(root / name, root / "public" / name)

# An allowlist keeps archives, local credentials and dependency trees out.
files = [
    root / name
    for name in (
        "README.md",
        "POLICY.md",
        "LICENSE",
        "CHANGELOG.md",
        "Makefile",
        "Dockerfile",
        ".dockerignore",
        ".gitignore",
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "next.config.ts",
        "vite.config.ts",
        ".oxlintrc.json",
        ".oxfmtrc.json",
        "components.json",
        "public/favicon.svg",
        ".github/workflows/check.yml",
        "vercel.json",
        ".vercelignore",
        "AGENTS.md",
    )
]
for directory, pattern in (
    ("ledger", "*.py"),
    ("tests", "*.py"),
    ("scripts", "*.py"),
    ("docs", "*.md"),
    ("config", "*.json"),
    ("app", "*.tsx"),
    ("app", "*.css"),
    ("components", "*.tsx"),
    ("lib", "*.ts"),
    ("deploy", "*"),
):
    files.extend((root / directory).rglob(pattern))
with zipfile.ZipFile(root / "public/source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, "poly-metars/" + path.relative_to(root).as_posix())
print(f"Published review documents and {len(set(files))} source files.")
