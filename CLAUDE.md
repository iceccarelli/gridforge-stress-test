# gridforge-stress — agent/maintainer rules

Independent harness that verifies external-data handling and adversarially tests the GridForge-AI
Time-to-Power product. It is NOT a second implementation of the product.

## Layout
- `src/gridforge_stress/`: `registry` (schema-validated sources/scenarios), `fetch` (safe acquisition),
  `manifest` (provenance + atomic snapshots), validators per scenario (`queue`, `poi`, `hourly`, `ercot`,
  `scenarios_data`), `evidence` (sufficiency rubric), `product_adapter` (real product boundary), `doctor`, `cli`.
- `configs/` registry + catalogue; `schemas/` JSON Schemas (versioned `.../1`); `data/fixtures/` labelled fixtures.
- Tests: `tests/unit`, `tests/contracts`, `tests/integration`. Markers: unit, contract, integration, product, live.

## Commands (all tested)
`uv sync --locked --all-groups` · `uv run --locked ruff check .` · `ruff format --check .` · `mypy src` ·
`pytest` (offline; excludes `product` and `live`) · `pytest -m product` (needs `GRIDFORGE_PRODUCT_DIR`) ·
`gridforge-stress doctor|sources list|scenarios list|validate-fixtures`.
Live, read-only evidence (needs outbound network; in practice run via the `probe` workflow): `gridforge-stress probe <id>|--all [--peek-xlsx]`.

## Source-integrity rules (non-negotiable)
1. A source is "verified" only if a publisher page was actually read; record `verification.status` honestly.
   `live_fetch` may be `enabled` only with `publisher_page_read`; gated sources (ERCOT) never enable.
2. A download is `fetched_unparsed` (transport checks only); only `manifest.promote` after a content validator
   with no errors yields `fetched_validated`. Never claim a fetch succeeded unless a real request passed validation. A failed fetch never overwrites a
   valid snapshot. Retrieval status is separate from availability assumptions.
3. Acquisition takes registered source IDs only: https, port 443, registered host, public IPs only, capped
   size/time/redirects, content-type check, redacted credentials. Never add a "fetch any URL" path.
4. Credentials come from environment variables named in the registry; never in files, fixtures, logs or Git.
5. Every fixture is labelled synthetic or source-derived with attribution; raw downloads and generated data are
   git-ignored (`data/raw`, `data/generated`).
6. Outputs keep their label: queue stats are not load-connection probabilities; BA data is not site headroom;
   Cambium is modeled; tariffs are generic; weather is not cooling load; a POI listing is not an allocation.

## Integration boundary
Product is `../GridForge-AI` (override with `GRIDFORGE_PRODUCT_DIR`). Call only its real handlers
(`/v1/compare`, `/v1/screen`, `/v1/diff` via `product_adapter.call`). Product changes go on a feature branch, never `main`.

## Branch safety
No force-push, reset --hard, clean, or stash. Do not merge PRs. Do not choose a licence for the owner.

## Engineering loop
Inspect -> change -> run the exact documented commands -> fix failures (never suppress) -> review `git diff`
and `git diff --check` -> commit coherent verified changes.
