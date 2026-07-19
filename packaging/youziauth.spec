# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()

datas = [
    (str(ROOT / "config.example.ini"), "."),
    (str(ROOT / "assets"), "assets"),
]

gui_analysis = Analysis(
    [str(ROOT / "campus_auth_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
agent_analysis = Analysis(
    [str(ROOT / "campus_auth_agent.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

MERGE(
    (gui_analysis, "youziauth", "youziauth"),
    (agent_analysis, "youziauth-agent", "youziauth-agent"),
)

gui_pyz = PYZ(gui_analysis.pure)
agent_pyz = PYZ(agent_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="youziauth",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "yuzu_app.ico"),
)

agent_exe = EXE(
    agent_pyz,
    agent_analysis.scripts,
    [],
    exclude_binaries=True,
    name="youziauth-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "yuzu_app.ico"),
)

coll = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    agent_exe,
    agent_analysis.binaries,
    agent_analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="youziauth",
)
