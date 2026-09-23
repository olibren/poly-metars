# Validation

The suite covers policy selection, corrections, NIL, SPECI exclusion, local days,
DST, precision and rounding, raw timestamp consistency, transient failures and
content-addressed offline replay. Cloudflare and source tests additionally cover multiple
WMO bulletins in one TGFTP collective, SA/SP and correction isolation, rotating
filenames, queue retries, immutable revisions and interrupted publication.

Run `make check build`. Validate a deployed day using the manifest downloader described in [AUDIT.md](AUDIT.md), then
running `python3 -m ledger.audit` on the downloaded directory. Confirm that
`/data/index.json.generated_at` advances without redeploying the website, and each
source's last successful check advances independently of historical recovery.

A fresh publisher timestamp alone is not a successful source check. Inspect both.
No validation in this repository establishes a completeness guarantee, independent
geographic failover, third-party receipt-time attestation, or settlement readiness.
