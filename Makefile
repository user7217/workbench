export PATH := $(CURDIR)/.venv/bin:/opt/homebrew/bin:/usr/local/bin:$(PATH)
UV := $(shell PATH="/opt/homebrew/bin:/usr/local/bin:$$PATH" command -v uv 2>/dev/null || echo uv)

.PHONY: schema build test demo dev

schema:
	$(UV) run python -c "from pathlib import Path; from workbench.models import write_json_schemas; write_json_schemas(Path('schema'))"
	cd web && npm run generate-types

build: schema
	cd web && npm run build

test: build
	$(UV) run pytest -v

demo: build
	$(UV) run python demo/run_demo.py

dev:
	$(UV) run python demo/run_demo.py
