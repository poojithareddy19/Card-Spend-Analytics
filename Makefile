SHELL := /bin/bash
PY ?= .venv/bin/python
PROFILE ?= dev
DATA ?= data

.PHONY: setup profiles generate contracts ingest build load report bench all test test-fast lint typecheck check clean

setup: ## create the venv and install everything
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev,serve,docstore]"

profiles: ## show the scale profiles and their sizes
	$(PY) -m card_spend.cli profiles

generate: ## 1. synthesise the five raw feeds in their native formats
	$(PY) -m card_spend.cli generate --profile $(PROFILE) --out $(DATA)

contracts: ## check every feed against its contract without landing anything
	$(PY) -m card_spend.cli contracts --data $(DATA)

ingest: ## 2. land all five formats into the canonical Parquet lake
	$(PY) -m card_spend.cli ingest --data $(DATA)

build: ## 3. dbt: snapshots, star schema, tests, then export the gold layer
	$(PY) -m card_spend.cli build --data $(DATA)

load: ## 4. load the marts into Postgres and the customer documents into Redis
	$(PY) -m card_spend.cli load --data $(DATA)

report: ## 5. run the reports and build the dashboard
	$(PY) -m card_spend.cli report --data $(DATA) --out out

bench: ## 6. benchmark every access pattern across every reachable store
	$(PY) -m card_spend.cli bench --data $(DATA) --out docs/benchmarks/results.md

all: generate ingest build load report ## the whole pipeline end to end

bench-dataset: ## build the 50M row benchmark dataset (roughly an hour, ~15 GB)
	$(MAKE) all PROFILE=bench DATA=data-bench
	$(PY) -m card_spend.cli bench --data data-bench --out docs/benchmarks/results.md

test: ## everything, including the dbt builds
	$(PY) -m pytest tests -q

test-fast: ## skip anything that shells out to dbt
	$(PY) -m pytest tests -q -m "not slow and not bench"

lint:
	.venv/bin/ruff check src tests
	.venv/bin/ruff format --check src tests

typecheck:
	.venv/bin/mypy

check: lint typecheck test ## everything CI runs

clean:
	rm -rf data data-bench out target logs .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
