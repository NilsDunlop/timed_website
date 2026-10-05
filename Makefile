.PHONY: dev test-cluster test test-e2e test-data gen-api lint

dev:
	@echo "The combined connector/web dev command is added in M4."

test-cluster:
	@echo "The local SSH/Slurm test cluster is added in M4."

test:
	uv run pytest
	pnpm -C apps/web check
	pnpm -C apps/web build

test-e2e:
	@echo "The Playwright end-to-end suite is added in M6."

test-data:
	uv run python scripts/make_test_data.py

gen-api:
	@echo "Typed API generation is added in M6."

lint:
	uv run ruff check .
	pnpm -C apps/web check
