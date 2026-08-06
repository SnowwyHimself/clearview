# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ClearView — builds a standalone desktop app.

Cross-platform: the same spec is used for the macOS build and the Windows CI
build. It bundles the Real-ESRGAN engine (native lib + model files), the web UI,
and platform ffmpeg/ffprobe binaries so the app needs no external setup.

Paths are all relative so no build-machine path leaks into the artifact.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# --- Collect the Real-ESRGAN package (native .so/.pyd + models/*.bin) --------
re_datas, re_binaries, re_hidden = collect_all("realesrgan_ncnn_py")

# --- Bundled ffmpeg/ffprobe (placed at the bundle root for _MEIPASS) ---------
tool_dir = Path("build_assets") / ("win" if IS_WIN else "mac")
tool_ext = ".exe" if IS_WIN else ""
tool_binaries = [
    (str(tool_dir / f"ffmpeg{tool_ext}"), "."),
    (str(tool_dir / f"ffprobe{tool_ext}"), "."),
]

# --- App source + web UI -----------------------------------------------------
datas = re_datas + [("static", "static")]
binaries = re_binaries + tool_binaries
hiddenimports = (
    re_hidden
    + collect_submodules("uvicorn")
    + ["main", "upscaler", "jobs", "webview"]
)

icon_mac = "packaging/clearview.icns"
icon_win = "packaging/clearview.ico"

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClearView",
    console=False,
    disable_windowed_traceback=False,
    icon=(icon_win if IS_WIN else icon_mac),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ClearView",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="ClearView.app",
        icon=icon_mac,
        bundle_identifier="com.snowwy.clearview",
        info_plist={
            "CFBundleName": "ClearView",
            "CFBundleDisplayName": "ClearView",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
        },
    )
