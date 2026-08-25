# -*- mode: python ; coding: utf-8 -*-
import glob
import os
import sys
from pathlib import Path

BASE_DIR = Path.cwd()

# ── Vendor binaries (ffmpeg, ffprobe, smartctl, LibreHardwareMonitor) ─────────
# Automatically bundle any .exe/.dll files found in vendor/ subdirectories so the
# standalone executable ships with all required third-party tools.
vendor_binaries = []
vendor_dir = BASE_DIR / 'vendor'
for exe_path in glob.glob(str(vendor_dir / 'ffmpeg' / '*.exe')):
    vendor_binaries.append((exe_path, os.path.join('vendor', 'ffmpeg')))
for exe_path in glob.glob(str(vendor_dir / 'smartmontools' / '*.exe')):
    vendor_binaries.append((exe_path, os.path.join('vendor', 'smartmontools')))
for exe_path in glob.glob(str(vendor_dir / 'librehardwaremonitor' / '*.exe')):
    vendor_binaries.append((exe_path, os.path.join('vendor', 'librehardwaremonitor')))
for dll_path in glob.glob(str(vendor_dir / 'librehardwaremonitor' / '*.dll')):
    vendor_binaries.append((dll_path, os.path.join('vendor', 'librehardwaremonitor')))
if vendor_binaries:
    print(f'[Spec] Bundling {len(vendor_binaries)} vendor binary(ies): '
          f'{", ".join(os.path.basename(p) for p, _ in vendor_binaries)}')
else:
    print('[Spec] WARNING: No vendor binaries found in vendor/. '
          'ffmpeg/smartctl/LibreHardwareMonitor will NOT be embedded in the exe.')

datas = [('frontend', 'frontend')]
try:
    import customtkinter
    ctk_dir = os.path.dirname(customtkinter.__file__)
    datas.append((ctk_dir, 'customtkinter'))
except Exception:
    pass

hidden_imports = [
    'backend',
    'backend.main',
    'backend.config',
    'backend.telemetry',
    'backend.file_manager',
    'backend.download_engine',
    'backend.terminal_emulator',
    'backend.nas_generator',
    'backend.setup_manager',
    'backend.speedtest_service',
    'backend.drive_health',
    'backend.media_service',
    'backend.ytdlp_service',
    'backend.aria2_client',
    'backend.icon_utils',
    'backend.server_runner',
    'backend.embedded_tools',
    'generate_demo_data',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.wsproto_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'fastapi',
    'fastapi.applications',
    'fastapi.routing',
    'fastapi.staticfiles',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'starlette',
    'starlette.applications',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    'starlette.responses',
    'multipart',
    'multipart.multipart',
    'pydantic',
    'psutil',
    'aiofiles',
    'websockets',
    'humanize',
    'PIL',
    'pystray',
    'webview',
    'customtkinter',
    'darkdetect',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'anyio._backends._trio',
    'anyio._core',
    'anyio._core._eventloop',
    'anyio._core._fileio',
    'anyio._core._sockets',
    'anyio._core._streams',
    'anyio._core._synchronization',
    'anyio._core._tasks',
    'anyio._core._testing',
    'anyio._core._typedattr',
    'anyio.abc',
    'anyio.from_thread',
    'anyio.to_thread',
    'sniffio',
]

a = Analysis(
    ['gui_launcher.py'],
    pathex=[],
    binaries=vendor_binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[str(BASE_DIR / 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pygments', 'IPython', 'ipykernel', 'matplotlib', 'scipy', 'pygame',
        'jupyter', 'jupyter_client', 'jupyter_core', 'tkinter.test', 'unittest',
        'django', 'flask', 'docker', 'dipy', 'h5py', 'nibabel', 'esphome',
        'aioesphomeapi', 'zeroconf', 'cryptography', 'setuptools', 'distutils',
        'pkg_resources', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'shiboken2',
        'shiboken6', 'pyqtgraph', 'PySimpleGUI'
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiskPulse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['diskpulse.ico'],
)

