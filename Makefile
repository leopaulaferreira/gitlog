PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup test test-integration lint format format-check check run migrate up down compose-check

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

migrate:
	$(BIN)/python -m ingestion.main migrate

up:
	docker compose up -d --wait

down:
	docker compose down

# Validate without creating .env or printing interpolated credentials.
compose-check:
	docker compose --env-file .env.example config --quiet
