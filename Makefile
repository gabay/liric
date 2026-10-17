.PHONY: help install lint check format test run docker-build docker-run clean

# Default shell
SHELL := /bin/bash

# Default target
help:
	@echo "Available targets for liric:"
	@echo "  make install       Install dependencies using uv"
	@echo "  make lint          Run linter (ruff check)"
	@echo "  make format        Auto-format code and fix lint issues"
	@echo "  make check         Run formatting and lint checks"
	@echo "  make test          Run pytest test suite"
	@echo "  make run           Run liric pipeline (scan + fetch + apply)"
	@echo "  make docker-build  Build Docker image"
	@echo "  make docker-run    Run liric inside Docker container"
	@echo "  make docker-deploy Build and push Docker image"
	@echo "  make clean         Remove build artifacts, caches, and test artifacts"

install:
	uv sync

lint:
	uv run ruff check liric tests

format:
	uv run ruff check --fix liric tests
	uv run ruff format liric tests

check:
	uv run ruff check liric tests
	uv run ruff format --check liric tests

test:
	uv run pytest

run:
	uv run liric run

docker-build:
	docker build -t liric:latest .

docker-run:
	docker run --rm -it -v $$(pwd)/music:/music -v $$(pwd)/config:/config liric:latest

clean:
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
