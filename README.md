# gridforge-stress

Independent source-integrity and adversarial acceptance harness for GridForge-AI's Time-to-Power product.
It validates external-data handling offline, records provenance, and checks that the real product never asserts
more than its evidence supports.

**Status:** foundation with first live evidence. Publisher pages for 7 of 9 sources have been read via the `probe` workflow;
no live dataset has been downloaded or parsed yet and live fetch is disabled everywhere (see [docs/data-sources.md](docs/data-sources.md)).
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

## Drive it from an agent (MCP)
`uv run --locked gridforge-stress mcp` serves the harness as an MCP server over stdio (no extra dependency); `.mcp.json`
registers it for Claude Code. Tools: `list_sources`, `get_source`, `list_scenarios`, `doctor`, `validate_fixtures`,
`assess_evidence`, `parse_queue_rows`, `product_call` (the REAL GridForge-AI `compare`/`screen`/`diff` handlers, with a
claim gate against an evidence level) and `probe_source` (off unless the operator sets `GRIDFORGE_STRESS_ALLOW_LIVE=1`).
There is deliberately no tool that fetches an arbitrary URL. Inputs are schema-validated; failures are tool errors, never
tracebacks.

## First working example
`tests/integration/test_product_ttp.py` feeds a synthetic reference hall through the product's real
`/v1/compare` handler and checks the date (160 weeks, set by the transformer lead time) against an independent
derivation, that every lead time stays an assumption (E0, `indicative`), and that removing a lead time removes the date.

## Source limitations
Live acquisition is disabled for every source (`gridforge-stress fetch <id>` refuses). Read-only page evidence comes from
`gridforge-stress probe <id>` / the `probe` workflow. ERCOT is registration-gated and blocked. EIA/OpenEI need keys
in `EIA_API_KEY` / `OPENEI_API_KEY`. Parsers work on declared column mappings and synthetic fixtures; the real
LBNL workbook layout is being established with `probe lbnl-queued-up --peek-xlsx`.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).
