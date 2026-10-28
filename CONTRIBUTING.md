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
`pytest -m product` needs a GridForge-AI checkout (`GRIDFORGE_PRODUCT_DIR`, default `../GridForge-AI`). The opt-in
`product-integration` workflow checks out the public product repository at the ref you name and runs those tests;
it is read-only and needs no secret. If the product repository is made private, give its checkout step a `token:`
backed by a read-only contents secret; do not add one while it is public.

## Pull requests
Describe evidence (commands run, results), list unverified behaviour, and keep CI offline.
