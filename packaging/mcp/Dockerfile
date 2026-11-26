# The Workbench claude.ai connector (remote MCP server); see docs/web-connector.md.
# Build from the repository root:  docker build -f packaging/mcp/Dockerfile -t workbench-mcp:local .
# No web UI is needed, only the Python package with its `mcp` extra, installed from uv.lock.

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first, so a code change does not reinstall them
COPY pyproject.toml uv.lock README.md ./
COPY packaging/hatch_build.py ./packaging/hatch_build.py
RUN uv sync --locked --no-dev --extra mcp --no-install-project
COPY src/ ./src/
# --no-editable: the package goes into the venv, so the runtime image needs no source tree
RUN uv sync --locked --no-dev --extra mcp --no-editable

FROM python:3.13-slim
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    FASTMCP_CHECK_FOR_UPDATES=off \
    WORKBENCH_MCP_STATE_DIR=/state \
    WORKBENCH_AGENT_SOCKET=/agent/agent.sock \
    WORKBENCH_MCP_HOST=0.0.0.0 \
    WORKBENCH_MCP_PORT=8000

# No USER line: the process runs as container root ON PURPOSE. The container runs in the rootless
# Docker of the unprivileged host user `mcpsvc` (uid 1001), where container root is mapped to
# host uid 1001 and has no host privileges. The dashboard creates the agent socket mode 0600,
# owned by uid 1001, so container root is exactly the identity that may connect to it; a
# non-root container user would map to a subordinate uid and be refused. Everything else is
# locked down at run time (packaging/mcp/compose.yml): read-only root filesystem, all
# capabilities dropped, no-new-privileges, resource limits, and only the agent socket directory
# and the state volume mounted.
VOLUME ["/state"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/.well-known/oauth-authorization-server', timeout=4)"]
CMD ["python", "-m", "workbench.mcp"]
