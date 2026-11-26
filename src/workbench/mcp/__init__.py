"""Remote MCP server for claude.ai: a custom connector that records progress on the dashboard.

It reaches the dashboard only through the restricted agent API on a Unix socket and lets in
only the one GitHub account named in its configuration (docs/web-connector.md). Needs the `mcp`
extra (`pip install 'workbench-dashboard[mcp]'`); `python -m workbench.mcp` runs it.
"""
