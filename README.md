# gridforge-stress

Independent source-integrity and adversarial acceptance harness for GridForge-AI's Time-to-Power product.
It validates external-data handling offline, records provenance, and checks that the real product never asserts
more than its evidence supports.

**Status:** foundation. No live source has been verified or fetched yet (see [docs/data-sources.md](docs/data-sources.md)).
No licence file is present; the owner has not chosen one.

## Fast start
```bash
uv sync --locked --all-groups
uv run --locked gridforge-stress doctor
uv run --locked gridforge-stress sources list
uv run --locked gridforge-stress scenarios list
uv run --locked gridforge-stress validate-fixtures
uv run --locked pytest                 # offline, deterministic
```
Real-product slice (needs a GridForge-AI checkout; default `../GridForge-AI`):
```bash
GRIDFORGE_PRODUCT_DIR=/path/to/GridForge-AI uv run --locked pytest -m product
```

## First working example
`tests/integration/test_product_ttp.py` feeds a synthetic reference hall through the product's real
`/v1/compare` handler and checks the date (160 weeks, set by the transformer lead time) against an independent
derivation, that every lead time stays an assumption (E0, `indicative`), and that removing a lead time removes the date.

## Source limitations
Live acquisition is disabled for every source until its publisher page has been read and recorded
(`gridforge-stress fetch <id>` refuses otherwise). ERCOT is registration-gated and blocked. EIA/OpenEI need keys
in `EIA_API_KEY` / `OPENEI_API_KEY`. Parsers work on declared column mappings and synthetic fixtures; the real
LBNL workbook layout is unverified.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).
