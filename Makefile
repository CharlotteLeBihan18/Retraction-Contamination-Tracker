.PHONY: help venv install run docker-build docker-run docker-down docker-logs test test-unit lint format clean tree

PYTHON ?= python3
VENV := .venv
ACTIVATE := . $(VENV)/bin/activate
TAG ?= retraction-tracker-api:dev
LINT_PATHS := app scripts

help:
	@echo "Retraction Contamination Tracker - available commands:"
	@echo "  make venv           Create the local virtualenv (.venv)"
	@echo "  make install        Install runtime + dev requirements into .venv"
	@echo "  make run            Run FastAPI locally (uvicorn) on :8000"
	@echo "  make docker-build   Build the API Docker image (TAG=$(TAG))"
	@echo "  make docker-run     Start the full stack (Neo4j, API, Nginx) in the background"
	@echo "  make docker-down    Stop the stack (data is kept in Docker volumes)"
	@echo "  make docker-logs    Follow the logs of all services"
	@echo "  make test           Run the whole pytest suite with coverage"
	@echo "  make test-unit      Run only the tests that need no running services"
	@echo "  make lint           Run pylint (must score >= 9.5)"
	@echo "  make format         Format code with black"
	@echo "  make clean          Remove caches and temporary files"
	@echo "  make tree           Show the project tree (depth 3)"

venv:
	@if [ ! -d "$(VENV)" ]; then \
		$(PYTHON) -m venv $(VENV); \
		$(ACTIVATE) && pip install --upgrade pip; \
		echo "Created $(VENV)"; \
	else echo "$(VENV) already exists"; fi
	@echo "To activate: source $(VENV)/bin/activate"

install: venv
	@$(ACTIVATE) && pip install -r requirements-dev.txt

run:
	@$(ACTIVATE) && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

docker-build:
	docker build -t $(TAG) .

docker-run:
	@if [ ! -f .env ]; then echo "Missing .env - run: cp .env.example .env"; exit 1; fi
	docker compose up --build -d
	@echo "API (via Nginx):  http://localhost/docs"
	@echo "Neo4j Browser:    http://localhost:7474"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

test:
	@$(ACTIVATE) && pytest

test-unit:
	@$(ACTIVATE) && pytest -m "not neo4j and not containers"

lint:
	@$(ACTIVATE) && pylint $(LINT_PATHS)

format:
	@$(ACTIVATE) && black app scripts tests -l 120

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} \; || true
	find . -type f -name "*.pyc" -delete || true
	rm -rf .pytest_cache .coverage coverage.xml htmlcov

tree:
	@if command -v tree >/dev/null 2>&1; then \
		tree -L 3 -I "node_modules|dist|.git|.venv|__pycache__"; \
	else find . -maxdepth 3 -not -path '*/\.*' -not -path './data/*' | sort; fi
