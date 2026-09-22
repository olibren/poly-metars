# poly-metar

Build an alternate resolution source for Polymarket’s METAR-based high and low
temperature markets. It is a proposed source, not an adopted Polymarket service.

Prioritize lightweight, simple, auditable, reliable and robust operation. Handle
heavy bot traffic through cached public data, independently of collection.
Keep the Cloudflare deployment easy to audit, adopt and maintain: Polymarket
must be able to own and operate it entirely without us or our accounts/access.

- Follow `POLICY.md` and `config/policy.json` for deterministic source selection.
- Preserve raw evidence, receipt times and revisions; backfill gaps without
  delaying live collection. Never estimate missing observations.
- Keep trading code, credentials, runtime data and secrets out of this repo.
- Read `docs/OPERATIONS.md` before operational changes; update `CHANGELOG.md`.
- Before publishing, run `make check build` and a meaningful offline audit replay.
- Deploy only this project; never alter unrelated trading services.
