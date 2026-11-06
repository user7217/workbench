# PyInstaller spec: freezes the workbench CLI and server, with the built web UI, into one folder
# (desktop/build/workbench/) that the desktop app ships and runs. A folder rather than a single
# file, so the binary starts at once: agent hooks run `workbench hook-check` after every turn.
#
#   make desktop-binary        (builds web/dist first; see the Makefile)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821 - SPECPATH is set by PyInstaller
WEB_DIST = ROOT / "web" / "dist"
if not (WEB_DIST / "index.html").is_file():
    raise SystemExit("web/dist is missing: run `npm run build` in web/ first")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "pyinstaller" / "entry.py")],
    pathex=[str(ROOT / "src")],
    # Agent prompt assets and demo data, found next to the modules as in an installed wheel
    datas=collect_data_files("workbench") + [(str(WEB_DIST), "workbench/web_dist")],
    # uvicorn picks its loop, protocol and lifespan modules at runtime
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("workbench"),
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="workbench",
    console=True,
    strip=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="workbench", strip=False, upx=False)  # noqa: F821
