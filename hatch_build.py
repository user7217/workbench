"""Hatch build hook: ship the built web UI (web/dist) inside the wheel as workbench/web_dist.

So a pip or `uv tool install` gets the dashboard too, not only the Docker image. Build the UI
first (`cd web && npm run build`); without it the wheel is built without a UI and says so.
Editable installs skip this: they serve web/dist from the checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebUIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        dist = Path(self.root) / "web" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "workbench/web_dist"
        else:
            self.app.display_warning(
                "web/dist is not built, so this wheel has no dashboard UI "
                "(run `cd web && npm run build` first)."
            )
