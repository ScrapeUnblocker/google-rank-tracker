.PHONY: install lint format test run dry-run digest status report clean

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

run:
	google-rank-tracker run

dry-run:
	google-rank-tracker run --dry-run

digest:
	google-rank-tracker digest --dry-run

status:
	google-rank-tracker status

report:
	google-rank-tracker report --out site

clean:
	rm -rf build dist site src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
