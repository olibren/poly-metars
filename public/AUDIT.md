# Reproduce a reading

Every row links to the exact government response retained by the collector.
The **Audit manifest** link identifies an immutable airport-day revision: its
reports, receipts, policy, airport registry, source-code hashes and raw-body hashes.

Clone the public repository, copy that manifest URL from the selected day, then run:

```sh
python3 scripts/download_audit.py 'https://SITE/data/revisions/HASH/audit.json' work/audit
python3 -m ledger.audit work/audit
```

The first command downloads only that revision and its original evidence and checks
the result. The second repeats the check entirely offline. Python 3.9 or later is
sufficient for the verifier; the Cloudflare deployment tools use Python 3.12+.

The verifier checks raw response hashes, receipt and report identities, reparses
the original METARs, and recomputes the source hierarchy, each selected reading,
and the daily high and low. It rejects mismatches. It does not infer absent reports.

Replay preserves the policy embedded in the manifest. Legacy v1 report identities
remain verifiable even when their original AWC JSON contains a receipt time that
was not normalized at collection. V2 records expose `source_received_at`; the
verifier checks that exact value against the original JSON, including subsecond
precision. Changing a normalized source time and recomputing its report hash cannot
make an unsupported latest-version claim pass replay.

Cloudflare stores raw evidence once and shares it across revisions. This avoids
copying large global bulletins into thousands of overlapping ZIP files. The
downloaded directory is the complete portable audit bundle; zip it if desired.
Download it before the retention window expires: the service keeps the last 30
days and complete local boundary days, with buffered archive cleanup. Older public
revision and evidence URLs are not permanent. A downloaded bundle remains usable
offline after the public copy expires.

Hashes detect alterations relative to the manifest. They are not government
signatures, proof that nothing was omitted, or independent proof of collection
time. The government agencies publish the observations; the operator controls
collection and publication. V3 locked results pin a strict midnight cutoff; legacy historical results retain
their original provisional policy. Archived revisions are never silently rewritten by the application.

V3 locked manifests use `poly-metars-day-v2`: the revision hash also pins
`finalization.cutoff_at` and the per-report database `accepted_at` map. Replay
rejects acceptance at or after cutoff, retrieval after cutoff, or acceptance before
retrieval. The selected extrema still use the same source hierarchy and rounding.
Acceptance metadata is operator-recorded provenance, not an independent signed
attestation or proof that every upstream observation was collected.

V4 keeps the same locked-manifest schema, with a pinned `reason`, `deadline_at`
and optional `trigger` inside `finalization`. Publication-triggered locks embed
`trigger_manifest`, the original audit manifest for the first next-day revision,
and include its raw-body hashes in the outer evidence list. The downloader therefore
fetches all evidence needed to replay both days. Replay validates the triggering
report was selected and eligible, the airport-local following date, the publication
cutoff/deadline, acceptance boundaries and the v4 source order and rounding. Older
v3 manifests still replay against their midnight policy without alteration.
