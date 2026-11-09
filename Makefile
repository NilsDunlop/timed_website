.PHONY: dev test-cluster test test-e2e test-data gen-api check-api lint

# Empty: config/profiles.yaml plus the optional ~/.timed/profiles.local.yaml.
PROFILES ?=

dev:
	uv run python scripts/dev.py $(if $(PROFILES),--profiles "$(PROFILES)",)

test-cluster:
	uv run python dev/test_cluster/server.py

test:
	uv run pytest
	pnpm -C apps/web check
	pnpm -C apps/web build

test-e2e:
	pnpm -C apps/web test

test-data:
	uv run python scripts/make_test_data.py

gen-api:
	pnpm -C apps/web gen:api

check-api: gen-api
	git diff --exit-code -- apps/web/src/lib/api

lint:
	uv run ruff check .
	pnpm -C apps/web check
