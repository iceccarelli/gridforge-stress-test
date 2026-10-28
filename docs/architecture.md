# Architecture

```
configs/sources.json ──► registry ──► fetch (guards) ──► manifest ──► snapshot store (atomic)
fixtures + parsers ──► Findings ──► evidence.assess ──► product_adapter ──► GridForge-AI handle_compare
```
- **registry**: JSON Schema validation plus cross-checks (host in allowed_hosts, gated sources never live,
  live only when the publisher page was read).
- **fetch**: injectable resolver/transport; every hop re-validated; IPs pinned after validation (blocks DNS rebinding).
- **evidence**: ordered levels none < context_only < indicative < site_supported. Only fresh site-specific
  evidence (utility letter, load study, allocation document) reaches site_supported; stale lowers; failed is excluded;
  modeled and region data never raise it.
- **product_adapter**: runs the real product handler in a clean subprocess and gates its claims against evidence.

## Product findings (confirmed from source)
- Intake lead times are always created as E0 (`gridforge/intake/loader.py`, transformer ~L367, BTM ~L394), so no external
  evidence can currently lift a product date above "assumed lead time, not a date". An evidence-carrying lead-time field
  would be a deliberate product change; it is not made here because no source is verified.
- Observed by `tests/integration/test_product_adversarial.py` (pinned as strict xfail so a product fix flips them):
  `contracted_MW` above firm capacity raises no caveat, and a numeric string for `firm_capacity_MVA` is silently coerced
  although `true` and `NaN` are refused.
- Verified invariants through the real handlers: public-tier screen carries no weeks; Hall Watch diff is antisymmetric
  and deterministic; capacity is monotone in firm/contracted power and capped by the smaller; the max-compute leader is
  stable across 27 lead-time combinations; a "fastest" leader is never named on tied dates.
- Evidence-gap detail for lead times: a date reads as supported only if EVERY taken step's lead time is above E0, so
  fixing the binding transformer alone cannot lift the grade (reported by the integration-seam investigation; not re-run here).

## Agent interface
`mcp_server` is a stdlib JSON-RPC 2.0 / MCP stdio server over the same functions the CLI uses; it adds no capability the
harness rules forbid (no arbitrary-URL fetch; live reads need operator opt-in; product calls only to the allow-listed
real handlers; schema-validated inputs; output cap; internals never returned). Where a standard CLI/API/MCP does not
exist for something we need (e.g. our own evidence rubric, source registry or product claim gate), this is the pattern:
build a small typed interface on the harness's own tested functions rather than a one-off script.
