# Data sources

State as of 2026-10-31. Evidence comes from the `probe` workflow (`.github/workflows/probe.yml`), which reads each
registered publisher page on a GitHub runner through the harness's guarded fetch layer (registered hosts only, public
IPs only, size-capped, no credentials, nothing stored). The sandbox where this repo is authored has no route to these
hosts, so a probe run is the only way pages get read. `configs/sources.json` records, per source, what was actually
read and which run read it; anything not shown stays `UNVERIFIED`.

| id | access | publisher page read? | live fetch | key findings (see registry for sources) |
|---|---|---|---|---|
| lbnl-queued-up | public | yes, via `eta-publications.lbl.gov` (now the registered locator; `emp.lbl.gov/queues` returns 403 to automated requests and is not registered) | disabled | CC BY 4.0, attribute LBNL + GridTracker; data through end of 2025; workbook declares 15,571,236 bytes; project sheet '03. Complete Queue Data' (38,201 rows, 30 columns); five measured statuses incl. `unknown` (codebook lists four); status/date conflicts exist in the data |
| caiso-facility-library | public | yes | disabled | index of 14 documents incl. Cluster 16 POI PDFs and two xlsx |
| caiso-pge-c16-poi | public | headers only (PDF text not extracted) | disabled | Last-Modified 2026-01-16; listing is not an allocation |
| eia-930 | API key (`EIA_API_KEY`) | yes | disabled | hour-ending timestamps in each BA's chosen local time zone |
| eia-api | API key | yes | disabled | free key by registration, `api_key` request parameter |
| ercot-data-portal | registration | no (blocked by policy) | blocked | account + subscription key + token per search snippets only |
| nrel-cambium | unverified | yes, via the official NLR Cambium page | disabled | 2024 release (fifth annual edition); viewer URL is an app shell |
| openei-urdb | API key | yes | disabled | `api_key` required (free); docs page last modified 2017 |
| nws-api | public | yes | disabled | User-Agent with contact required |
| eia-epm | public | no (one plain GET outside the harness fetch path, 2026-10-30; not a probe run) | disabled | Electric Power Monthly Table 5.6.A (release of October 23, 2026) was parsed into the `eia_retail_price_context` fixture; licence UNVERIFIED, status stays `unverified` until a probe run reads it |

`live_fetch: enabled` is allowed only for a source whose publisher page was read AND with a tested reason; none is
enabled. Reading a page is not the same as downloading or parsing its data. The only dataset read through the harness so far is the LBNL workbook, inspected
in memory by explicit request (`probe lbnl-queued-up --peek-xlsx|--profile|--queue-stats`); it was never stored or promoted to `fetched_validated`.

To verify or extend a source: `gridforge-stress probe <id>` (or dispatch the `probe` workflow), read the output, update
the registry fields the output supports, and cite the run in `verification.basis`.

## Probe targets and outcomes

A registry record separates what the probe may read: the locator and `probe_urls` are publisher **pages** (capped at 3 MB);
`data_urls` are **dataset downloads**, never read by a page probe and only inspected in memory when asked explicitly
(`--peek-xlsx`, `--profile`, `--queue-stats`), within the source's own `max_bytes` and the workbook safety limits.
A URL cannot be both. Every printed record carries `source_id`, `url`, `kind` (`publisher_page` | `dataset_inspection`),
`retrieval_status`, `at` and, when not read, the truthful `reason` and `http_status`.

| outcome | meaning | effect on exit code |
|---|---|---|
| read | the target was fetched in memory and its content summarised | none |
| `BLOCKED_BY_POLICY` | the registry deliberately gates the source (ERCOT `blocked_until_authorized`) | none: expected |
| `NOT_READ` | HTTP error, redirect off the registry, non-public address, oversize, unsafe workbook, unknown id, or no registered dataset URL | exit 3: actionable |

The last line printed is a `probe_summary` with the counts and this policy.
