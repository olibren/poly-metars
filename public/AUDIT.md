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

Cloudflare stores raw evidence once and shares it across revisions. This avoids
copying large global bulletins into thousands of overlapping ZIP files. The
downloaded directory is the complete portable audit bundle; zip it if desired.

Hashes detect alterations relative to the manifest. They are not government
signatures, proof that nothing was omitted, or independent proof of collection
time. The government agencies publish the observations; the operator controls
collection and publication. All results remain provisional under the current
policy. Archived revisions are never silently rewritten by the application.
