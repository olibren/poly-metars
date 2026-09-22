# poly-metars

This repository is a public weather observation ledger. It is independent of any
trading bot. Never add trading credentials, private bot code, or order execution.

- Source hierarchy and selection rules live in config/policy.json and POLICY.md.
- Preserve raw response bytes, receipt times, rejected observations and revisions.
- Live checks must not wait for historical recovery. Never imply complete coverage
  from an HTTP success, and never replace missing observations with estimates.
- Publish changes only after `make check build` and a meaningful offline audit replay.
- Never commit runtime data, database files, environment files or deployment secrets.
- Only deploy the project-specific collector. Do not restart unrelated services.
- Update CHANGELOG.md and the operating guide for operational changes.
