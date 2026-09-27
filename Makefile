PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: setup test lint format format-check check run up down compose-check

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install -e '.[dev]'

test:
	$(BIN)/python -m pytest

lint:
	$(BIN)/ruff check .

format:
	$(BIN)/ruff check --select I --fix .
	$(BIN)/black .

format-check:
	$(BIN)/black --check .

check: lint format-check test

# Show available project commands; ingestion arrives in Phase 2.
run:
	$(BIN)/python -m ingestion.main

up:
	docker compose up -d --wait

down:
	docker compose down

# Validate without creating .env or printing interpolated credentials.
compose-check:
	docker compose --env-file .env.example config --quiet
