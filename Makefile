# Developer shortcuts. See docs/development.md.

.PHONY: install test lint format check serve clean

install:            ## editable install with dev tools
	pip install -e '.[dev]'

test:               ## run the full test suite
	python3 -m pytest

lint:               ## static checks (what CI runs)
	ruff check src tests
	ruff format --check src tests

format:             ## auto-fix style and imports
	ruff check --fix src tests
	ruff format src tests

check: lint test    ## everything CI runs

serve:              ## run a local dev server
	python3 -m tts_gateway serve

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
