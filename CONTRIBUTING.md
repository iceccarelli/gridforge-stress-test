# Contributing

## Setup
`uv sync --locked --all-groups` (Python >=3.11,<3.14, see `.python-version`). Optional: `pre-commit install`.

## Standards
`ruff check`, `ruff format`, `mypy --strict` on `src`, offline `pytest`. Validators return `Finding`s; they never
silently repair or default data. Keep dependencies minimal; change them only with a refreshed `uv.lock`.

## Adding a source
Add a record to `configs/sources.json` (schema `schemas/source.schema.json`). Leave `live_fetch` as
`disabled_until_verified` and `verification.status` honest until you have read the publisher page (use `probe`; cite the
run in `verification.basis`). Add tests in `tests/contracts`. Credentials are env-var names only.

## Adding a scenario
Add to `configs/scenarios.json` with `must_not_claim`, implement a validator module, add unit tests including
negative cases, and a labelled fixture + manifest if needed.

## Fixtures
Small, labelled (`synthetic_fixture` with `SYNTHETIC` in limitations, or `source_derived_fixture` with attribution
and enough provenance to reconstruct). Never commit raw downloads. Regenerate the manifest checksum when edited.

## Real-product tests on a runner
`pytest -m product` needs the private GridForge-AI checkout. The opt-in `product-integration` workflow does that and
requires a repository secret `PRODUCT_REPO_TOKEN` (read-only contents access to iceccarelli/GridForge-AI). Without
the secret it stops with an explicit error; ordinary CI never needs it. Until the secret exists, the real-product
tests run only where `GRIDFORGE_PRODUCT_DIR` points at a local checkout.

## Pull requests
Describe evidence (commands run, results), list unverified behaviour, and keep CI offline.
