# Adoption by Polymarket

The trust objective is operator independence from weather traders and the original
developers. This design provides an auditable, operator-controlled publication
system. It does not create decentralized consensus or remove trust in Cloudflare
and the originating weather agencies.

Polymarket should deploy a reviewed commit into its own Cloudflare account, using
its own billing and domain, and own the source repository and release permissions.
A fresh deployment avoids inherited developer API tokens, webhooks and account
memberships. No developer-managed server or external service is required.

Before adoption, review the station mapping, routine-METAR restriction, integer
versus precise temperatures, timezone boundaries, rounding, correction handling,
source hierarchy, conflict handling and missing-data rules in POLICY.md. Establish
an explicit finalization and dispute policy; v4/v5 close revisions on the first eligible
next-day publication or the following date’s ET deadline. Review the differences
from weather.gov in WEATHER_GOV_COMPATIBILITY.md. Freeze/version the applicable
policy before the market opens.

Replay independently downloaded day bundles and review collection behavior through
real upstream outages, queue retries and delayed corrections. The repository tests
cover those failure contracts, but do not establish a market-resolution SLA.

At cutover, preserve old evidence and immutable revision URLs, record the deployed
commit and policy hashes publicly, enable operational alerts, and remove all former
developer/trader roles and deployment credentials. Polymarket alone should control
repo administration, deployments, Cloudflare resources, DNS and billing. External
contributors can propose changes through public pull requests without write access.

Readers can reproduce published selections offline and retain their own copies of
manifests and evidence. An independent public witness of manifest hashes would add
protection against later operator rewriting; it is not implemented or required for
this initial deployment. SHA-256 alone cannot prove that the operator received every
report or that an unobserved earlier version never existed.
