# Data sources

State as of 2026-10-27. **No publisher page was read** from the build sandbox (DNS failure or egress policy 403 for
every host), so entries come from search snippets and are marked `search_snippet_only` or `unverified` in
`configs/sources.json`. Nothing has been fetched.

| id | access | live fetch | note |
|---|---|---|---|
| lbnl-queued-up | public | disabled | workbook link, sheets, codebook unverified; CC BY 4.0 (LBNL + GridTracker) per snippet |
| caiso-facility-library | unverified | disabled | page not read |
| caiso-pge-c16-poi | public | disabled | informational tables; a listing is not an allocation |
| eia-930 / eia-api | API key (`EIA_API_KEY`) | disabled | timestamp convention unverified |
| ercot-data-portal | registration | blocked | account + subscription key + token per snippets |
| nrel-cambium | unverified | disabled | modeled scenarios; release/licence unverified |
| openei-urdb | API key (`OPENEI_API_KEY`) | disabled | generic tariffs, not contracted rates |
| nws-api | public | disabled | User-Agent required per snippets (`NWS_USER_AGENT`) |

To enable a source: read the publisher page, update the record (basis, units, time convention, licence),
set `verification.status` to `publisher_page_read`, then `live_fetch` to `enabled` with a test.
