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
