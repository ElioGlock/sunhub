# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: gera dist/Sunhub/ com
#   Sunhub.exe      app de desktop (sem console); também roda o sync do Agendador de Tarefas
#   sunhub-cli.exe  terminal e servidor MCP (stdio)
#
#   pip install ".[app]" pyinstaller
#   pyinstaller packaging/windows/sunhub.spec --noconfirm
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ICON = os.path.join(ROOT, "src", "sunhub", "desktop", "sunhub.ico")

hidden = collect_submodules("sunhub") + collect_submodules("mcp")
datas = collect_data_files("sunhub")

a = Analysis(
    [os.path.join(SPECPATH, "sunhub_entry.py")],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "fastembed", "onnxruntime", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Sunhub", console=False, icon=ICON)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="sunhub-cli", console=True, icon=ICON)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="Sunhub")
