export PATH := $(CURDIR)/.venv/bin:/opt/homebrew/bin:/usr/local/bin:$(PATH)
UV := $(shell PATH="/opt/homebrew/bin:/usr/local/bin:$$PATH" command -v uv 2>/dev/null || echo uv)

.PHONY: schema build test demo dev lint format desktop-binary desktop desktop-release

schema:
	$(UV) run python -c "from pathlib import Path; from workbench.models import write_json_schemas; write_json_schemas(Path('schema'))"
	cd web && npm run generate-types

build: schema
	cd web && npm run build

test: build
	$(UV) run pytest -v

demo: build
	$(UV) run python scripts/run_demo.py

dev:
	$(UV) run python scripts/run_demo.py

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	cd web && npm run lint

format:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .
	cd web && npm run format

# Desktop app: freeze the CLI and server (with the built UI) into desktop/build/workbench/, then
# bundle the menu-bar app around it. Output: desktop/src-tauri/target/release/bundle/{macos,dmg}/
desktop-binary: build
	$(UV) run --with pyinstaller pyinstaller packaging/pyinstaller/workbench.spec --noconfirm \
		--distpath desktop/build --workpath desktop/build/.pyinstaller

desktop: desktop-binary
	cd desktop && npm ci && npx tauri build

# As `desktop`, plus the signed update archive (Workbench.app.tar.gz + .sig) that installed apps
# download. Needs TAURI_SIGNING_PRIVATE_KEY and TAURI_SIGNING_PRIVATE_KEY_PASSWORD (docs/releasing.md).
desktop-release: desktop-binary
	cd desktop && npm ci && npx tauri build --config src-tauri/tauri.updater.conf.json
