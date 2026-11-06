"""Artificial environment and demo data generator.

The demo content (fake code repos, the seeded workbench-data entries and the fake GitHub
fixtures) lives as YAML and Markdown under data/; this package turns it into a working
environment.
"""

from __future__ import annotations

from workbench.demo.environment import init_demo_environment
from workbench.demo.fake_github import FakeGitHubServer

__all__ = ["FakeGitHubServer", "init_demo_environment"]
