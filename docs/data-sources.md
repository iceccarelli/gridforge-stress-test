# Data sources

State as of 2026-10-28. Evidence comes from the `probe` workflow (`.github/workflows/probe.yml`), which reads each
registered publisher page on a GitHub runner through the harness's guarded fetch layer (registered hosts only, public
IPs only, size-capped, no credentials, nothing stored). The sandbox where this repo is authored has no route to these
hosts, so a probe run is the only way pages get read. `configs/sources.json` records, per source, what was actually
read and which run read it; anything not shown stays `UNVERIFIED`.

| id | access | publisher page read? | live fetch | key findings (see registry for sources) |
|---|---|---|---|---|
| lbnl-queued-up | public | yes, via `eta-publications.lbl.gov` (the locator `emp.lbl.gov/queues` returns 403 to automated requests) | disabled | CC BY 4.0, attribute LBNL + GridTracker; data through end of 2025; workbook declares 15,571,236 bytes; project sheet '03. Complete Queue Data' (38,201 rows, 30 columns); five measured statuses incl. `unknown` (codebook lists four); status/date conflicts exist in the data |
| caiso-facility-library | public | yes | disabled | index of 14 documents incl. Cluster 16 POI PDFs and two xlsx |
| caiso-pge-c16-poi | public | headers only (PDF text not extracted) | disabled | Last-Modified 2026-01-16; listing is not an allocation |
| eia-930 | API key (`EIA_API_KEY`) | yes | disabled | hour-ending timestamps in each BA's chosen local time zone |
| eia-api | API key | yes | disabled | free key by registration, `api_key` request parameter |
| ercot-data-portal | registration | no (blocked by policy) | blocked | account + subscription key + token per search snippets only |
| nrel-cambium | unverified | yes, via the official NLR Cambium page | disabled | 2024 release (fifth annual edition); viewer URL is an app shell |
| openei-urdb | API key | yes | disabled | `api_key` required (free); docs page last modified 2017 |
| nws-api | public | yes | disabled | User-Agent with contact required |

`live_fetch: enabled` is allowed only for a source whose publisher page was read AND with a tested reason; none is
enabled. Reading a page is not the same as downloading or parsing its data: no live dataset has been parsed yet.

To verify or extend a source: `gridforge-stress probe <id>` (or dispatch the `probe` workflow), read the output, update
the registry fields the output supports, and cite the run in `verification.basis`.
