# Validation

The suite covers policy selection, corrections, NIL, SPECI exclusion, local days,
DST, precision and rounding, raw timestamp consistency, transient failures and
content-addressed offline replay. Live-service tests additionally cover multiple
WMO bulletins in one TGFTP collective, SA/SP and correction isolation, rotating
filenames, restart persistence, immutable revisions and HTTP path isolation.

Run `make check build`. Validate a deployed day by downloading its bundle, extracting
it, and running `python3 -m ledger.audit` on the extracted directory. Confirm that
`/data/index.json.generated_at` advances without redeploying the website, and each
source's last successful check advances independently of historical recovery.

A fresh publisher timestamp alone is not a successful source check. Inspect both.
No validation in this repository establishes a completeness guarantee, independent
geographic failover, third-party receipt-time attestation, or settlement readiness.
