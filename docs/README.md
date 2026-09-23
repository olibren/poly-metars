# Documentation guide

For a first review, read the repository [README](../README.md) and current
[selection policy](../POLICY.md), then choose the task below.

| Task | Read |
|---|---|
| Independently reproduce a published temperature | [Audit guide](AUDIT.md) |
| Install, deploy, monitor or recover the current Cloudflare system | [Operations](OPERATIONS.md) |
| Adopt the system into Polymarket-owned accounts | [Handover](HANDOVER.md) |
| Run the validation workflow | [Validation](VALIDATION.md) |
| Understand finalization and the current disabled-locking mode | [Finalization contract](FINALITY_DESIGN.md) |
| Review the interface and differences from weather.gov | [Interface](INTERFACE_REVIEW.md), [weather.gov compatibility](WEATHER_GOV_COMPATIBILITY.md) |

## Compatibility references

`policies/` documents earlier policy versions; `../config/policies/` contains their
machine-readable definitions. These exist to explain and replay bundles that pin
older rules. Current behaviour is defined by `../POLICY.md` and
`../config/policy.json`. Do not use old policy documents as deployment instructions.

## Local development utilities

Production collection and publication run only in `cloudflare/`. The utilities in
`scripts/offline/` provide local evidence capture and fixture export used by the
regression tests. They are excluded from the Worker package and are not a second
production collector.

To review airport mappings against public market rules, run from the repository root:

```sh
python3 -m scripts.offline.catalog
```

This contacts the public market API, updates mapping metadata in
`config/airports.json`, and writes receipts to `work/catalog/` and a market report to
`work/market-review.json`. Review the resulting registry diff before committing;
ambiguous station mappings are reported rather than guessed. This utility does not
run automatically during builds, tests or deployment.
