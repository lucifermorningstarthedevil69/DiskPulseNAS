# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

BASE_DIR = Path.cwd()

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
    binaries=[],
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

