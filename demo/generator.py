"""CLI entry wrapper for demo environment generation (§11)."""

from __future__ import annotations

import sys
from pathlib import Path

from workbench.demo_env import init_demo_environment


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/workbench-demo")
    info = init_demo_environment(target)
    print(f"Initialized demo environment at {info['base_dir']}")


if __name__ == "__main__":
    main()
