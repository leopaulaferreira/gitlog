PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup test test-integration lint format format-check check run commits issues pull-requests ingest-all migrate up down compose-check coverage status

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install -e '.[dev]'

test:
	$(BIN)/python -m pytest

test-integration:
	$(BIN)/python scripts/test_integration.py

lint:
	$(BIN)/ruff check .

format:
	$(BIN)/ruff check --select I --fix .
	$(BIN)/black .

format-check:
	$(BIN)/black --check .

check: lint format-check test

run:
	$(BIN)/python -m ingestion.main repositories

commits:
	$(BIN)/python -m ingestion.main commits

issues:
	$(BIN)/python -m ingestion.main issues

pull-requests:
	$(BIN)/python -m ingestion.main pull-requests

ingest-all:
	$(BIN)/python -m ingestion.main all

migrate:
	$(BIN)/python -m ingestion.main migrate

up:
	docker compose up -d --wait

down:
	docker compose down

# Validate without creating .env or printing interpolated credentials.
compose-check:
	docker compose --env-file .env.example config --quiet

status:
	$(BIN)/python -m ingestion.main status

coverage:
	$(BIN)/python -m coverage erase
	$(BIN)/python -m coverage run --branch --source=ingestion -m pytest -m 'not integration' -q
	$(BIN)/python scripts/test_integration.py --coverage
	$(BIN)/python -m coverage report -m
