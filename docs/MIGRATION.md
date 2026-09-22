# Cloudflare migration record — 2026-09-22

The Cloudflare collector uses the existing source hierarchy, registry, METAR parser
and selection rules. The legacy collector remained available while Cloudflare was
created and tested. The new runtime does not call the old origin.

Before importing the saved legacy ledger, all **14,521 normalized reports** and
**10,307 receipts** were checked with `ledger.evidence.verify_evidence` against the
original response files. Report IDs and actual receipt times were preserved; rows
already collected by Cloudflare were not overwritten. Imported days were marked
for republication under the Cloudflare engine revision.

The original SQLite ledger is retained in the R2 bucket at
`legacy/ledger-2026-09-22.sqlite3`, with an import note at
`legacy/import-2026-09-22.json`. These are owner recovery artifacts. Legacy normalized
rows are durably represented there and in the public day manifests; the new
collector additionally writes each new normalized record under `reports/`.

The old raw evidence and immutable published revisions are copied into the same
R2 key layout. They remain auditable under their original engine hashes. The
legacy index is preserved separately under `legacy/`; it never overwrites the
Cloudflare current index. Runtime data and migration working files are excluded
from the source repository.

The verification suite covers response integrity, classification, corrections,
source conflicts, DST, rounding, repeated deliveries, lost queue messages, storage
write interruption, concurrent publishers and historical task priority. Additional
checks exercised Python Workers through Wrangler, fetched actual government data,
and replayed a day downloaded from the public Cloudflare site.

The final stopped-collector backup contains **18,196 reports** and **51,796 receipts**.
Its 3,675 additional reports were separately reparsed and verified before merging;
previously verified records were checked for unchanged contents. The compressed
original database is retained at `legacy/ledger-final-2026-09-22.sqlite3.gz`, and its
last public index at `legacy/index-final-2026-09-22.json`.

The old collector and backup timer stopped after the final backup at
2026-09-22T16:58:06Z. The EC2 instance is stopped; its disk and S3 backups remain
as rollback storage and can still incur storage charges. No AWS service is needed
by the Cloudflare runtime. The former Vercel address redirects paths and query
strings to Cloudflare.
