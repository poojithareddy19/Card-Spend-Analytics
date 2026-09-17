# A venv puts its interpreter in a different place on Windows, and `make setup` is the first thing
# anyone runs. Getting that wrong makes the repo look broken before a single line of it has run.
#
# Native Windows make runs its recipes through cmd.exe, which rejects a forward-slash command path
# outright: `.venv/Scripts/python` exits 9009, `.venv\Scripts\python` works. Running make from Git
# Bash, MSYS2 or WSL still sets OS=Windows_NT but uses a POSIX shell, where the backslashes are
# escapes instead. There is no single spelling that satisfies both, so pass the other one in:
#
#     make check PY=.venv/Scripts/python
ifeq ($(OS),Windows_NT)
  PY ?= .venv\Scripts\python.exe
  BOOTSTRAP_PY ?= python
else
  SHELL := /bin/bash
  PY ?= .venv/bin/python
  BOOTSTRAP_PY ?= python3
endif

PROFILE ?= dev
DATA ?= data

.PHONY: setup profiles generate contracts ingest build load report bench all test test-fast lint typecheck check clean

setup: ## create the venv and install everything
	$(BOOTSTRAP_PY) -m venv .venv
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

# Invoked as `python -m` rather than by binary name, so there is one spelling of the venv path in
# this file rather than four.
lint:
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

typecheck:
	$(PY) -m mypy

check: lint typecheck test ## everything CI runs

# Done in Python rather than with `rm -rf` and `find`, so one recipe works on both platforms. The
# comment sits above the target rather than inside the recipe, because cmd.exe has no `#` comment
# and would try to run the line.
clean: ## remove every generated artefact
	$(BOOTSTRAP_PY) -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ['data','data-bench','out','target','logs','.pytest_cache','.mypy_cache','.ruff_cache']]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
