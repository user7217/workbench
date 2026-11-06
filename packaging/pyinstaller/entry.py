"""Entry point of the frozen workbench binary that ships inside the desktop app."""

from workbench.cli import app

if __name__ == "__main__":
    app(prog_name="workbench")
